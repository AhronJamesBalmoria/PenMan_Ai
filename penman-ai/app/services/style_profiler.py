"""Adaptive feedback: a per-author stylometric baseline, computed in pure Python.

Writing style is measured with topic-robust features (function-word rates,
punctuation habits, sentence rhythm) instead of semantic embeddings, so a
topic change no longer looks like style drift. The profile stores a running
mean/M2/count (Welford) per author+essay type; no old essays are re-read.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import math
from datetime import UTC, datetime, timedelta
from typing import Any

from app.constants import DEFAULT_ESSAY_TYPE, normalize_essay_type
from app.db.supabase_client import execute_async, get_supabase_with_token
from app.services.gemini import generate_json  # Groq/OpenRouter fallback chain
from app.services.stylometry import FEATURE_NAMES, style_features

logger = logging.getLogger(__name__)

# A style baseline needs more than 100 words to be meaningful. Short drafts
# still save normally; they just are not folded into the writing profile.
MIN_PROFILE_WORDS = 100

# Drift checks need enough text for punctuation and function-word rates to be
# stable. 50 characters (~10 words) produced almost pure noise.
MIN_DRIFT_WORDS = 100

# Drift rule (named constants so tuning is obvious):
Z_THRESHOLD = 3.0          # a feature must be this many std devs from the mean
MIN_DRIFT_FEATURES = 3     # ...and at least this many features must breach
MIN_HISTORY_ESSAYS = 3     # no drift verdict before this many prior essays
STD_FLOOR = 0.01           # absolute floor on std to avoid early-variance blowups
STD_RELATIVE_FLOOR = 0.10  # ...plus a floor relative to the feature mean

PROFILE_TABLE = "author_profiles"

# The 5-minute throttle now gates only the tone/register LLM call, not the
# cheap local stylometric update.
LLM_REFRESH_INTERVAL = timedelta(minutes=5)


def _profile_is_fresh(profile: dict | None) -> bool:
    """True when the saved tone/register was written within the throttle window."""
    if not profile:
        return False
    raw = profile.get("updated_at")
    if not raw:
        return False
    try:
        updated = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return False
    if updated.tzinfo is None:
        updated = updated.replace(tzinfo=UTC)
    return datetime.now(UTC) - updated < LLM_REFRESH_INTERVAL


class StyleProfileError(RuntimeError):
    pass


def _as_float(value, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _clean_label(value, default: str) -> str:
    text = str(value or "").strip().lower()
    return text[:40] or default


def _content_hash(text: str) -> str:
    """Stable hash of an essay, used to avoid double-counting the same text."""
    return hashlib.sha256((text or "").strip().encode("utf-8")).hexdigest()


async def _extract_metrics(text: str) -> dict:
    """Use Groq/OpenRouter to extract tone, register, and complexity."""
    prompt = (
        "Respond with a single valid JSON object. Do not output conversational "
        "text, markdown, code fences, or explanations.\n"
        "Return exactly these four keys:\n"
        '{"tone": "one word describing the tone", '
        '"register": "one word describing the register", '
        '"avg_sentence_length": 15.0, '
        '"vocabulary_richness": 0.5}\n\n'
        "Text to analyze:\n"
        "<text>\n"
        f"{text[:10000]}\n"
        "</text>"
    )
    parsed, model_used, error = await generate_json(prompt, temperature=0.0)
    if error:
        raise StyleProfileError(f"All AI models failed: {error}")
    if not isinstance(parsed, dict):
        raise StyleProfileError("AI response was not a JSON object.")
    logger.info("[style_profile] metrics extracted using %s", model_used)
    return parsed


def _as_float_list(value: Any, size: int) -> list[float] | None:
    """Parse a jsonb/float8[] column into a fixed-size float list."""
    if not isinstance(value, (list, tuple)) or len(value) != size:
        return None
    try:
        return [float(item) for item in value]
    except (TypeError, ValueError):
        return None


def _welford_update(
    mean: list[float], m2: list[float], count: int, sample: list[float]
) -> tuple[list[float], list[float], int]:
    """Fold one sample into a running mean/M2 and return the new state."""
    new_count = count + 1
    new_mean, new_m2 = [], []
    for old_mean, old_m2, value in zip(mean, m2, sample, strict=True):
        delta = value - old_mean
        updated_mean = old_mean + delta / new_count
        updated_m2 = old_m2 + delta * (value - updated_mean)
        new_mean.append(updated_mean)
        new_m2.append(updated_m2)
    return new_mean, new_m2, new_count


def _effective_std(std: float, mean: float) -> float:
    return max(std, abs(mean) * STD_RELATIVE_FLOOR, STD_FLOOR)


# Friendly labels for the punctuation/contraction "use" features.
_USAGE_LABELS = {
    "comma use": "commas",
    "semicolon use": "semicolons",
    "colon use": "colons",
    "dash use": "dashes",
    "parenthesis use": "parentheses",
    "exclamation use": "exclamation marks",
    "question use": "question marks",
    "contraction use": "contractions",
}


def _frequency_phrase(mean: float, difference: float) -> str:
    """Natural language for a change in a per-word rate."""
    if abs(mean) < 1e-6:
        # Baseline rarely/never used it: describe the new presence directly.
        return "far more often than usual" if difference > 0 else "much less often than usual"
    pct = round(abs(difference) / mean * 100)
    if pct < 5:
        return "about the same as usual"
    return (f"about {pct}% more often than usual" if difference > 0
            else f"about {pct}% less often than usual")


def _describe_change(name: str, mean: float, value: float) -> str:
    """Turn one shifted feature into a short, human-readable sentence."""
    difference = value - mean
    if abs(difference) < 1e-9:
        return ""

    # Sentence rhythm.
    if name == "average sentence length" and mean > 0:
        pct = round(abs(difference) / mean * 100)
        if pct < 5:
            return ""
        return (f"your sentences are about {pct}% shorter than usual"
                if difference < 0
                else f"your sentences are about {pct}% longer than usual")
    if name == "sentence length variation":
        return ("your sentences are more uniform in length than usual"
                if difference < 0
                else "your sentences vary more in length than usual")
    if name == "average word length":
        pct = round(abs(difference) / mean * 100) if mean > 1e-9 else 0
        if pct < 5:
            return ""
        return (f"your words are about {pct}% longer than usual"
                if difference > 0
                else f"your words are about {pct}% shorter than usual")
    if name == "vocabulary richness":
        return ("your vocabulary is more varied than usual"
                if difference > 0
                else "your vocabulary is more repetitive than usual")
    if name == "average paragraph length":
        pct = round(abs(difference) / mean * 100) if mean > 1e-9 else 0
        if pct < 5:
            return ""
        return (f"your paragraphs are about {pct}% longer than usual"
                if difference > 0
                else f"your paragraphs are about {pct}% shorter than usual")

    # Punctuation / contraction rates.
    if name in _USAGE_LABELS:
        label = _USAGE_LABELS[name]
        phrase = _frequency_phrase(mean, difference)
        if phrase == "about the same as usual":
            return ""
        return f"you use {label} {phrase}"

    # Function words: name is the bare word (e.g. "the", "however").
    phrase = _frequency_phrase(mean, difference)
    if phrase == "about the same as usual":
        return ""
    return f'"{name}" shows up {phrase} in your writing'


async def update_user_style_profile(
    user_id: str,
    text: str,
    access_token: str,
    essay_type: str = DEFAULT_ESSAY_TYPE,
) -> dict:
    """Extract the author's style for one persona and upsert it.

    Profiles are stored per ``(user_id, essay_type)`` so a creative piece does
    not overwrite the baseline for an academic essay.
    """
    persona = normalize_essay_type(essay_type)
    stripped = (text or "").strip()
    logger.info(
        "[style_profile] background task started: user=%s persona=%s chars=%d",
        user_id,
        persona,
        len(stripped),
    )

    words = len(stripped.split())
    if words <= MIN_PROFILE_WORDS:
        logger.info(
            "[style_profile] skipped: %d words <= %d required",
            words,
            MIN_PROFILE_WORDS,
        )
        return {
            "error": "Text too short to extract a reliable style profile "
            "(needs more than 100 words)."
        }

    # 1. Compute stylometric features locally (CPU-bound; off the event loop).
    sample = await asyncio.to_thread(style_features, stripped)
    current_hash = _content_hash(stripped)
    logger.info("[style_profile] stylometric features: %d", len(sample))

    try:
        client = get_supabase_with_token(access_token)
    except Exception as exc:
        logger.exception("[style_profile] Supabase client failed: %s", exc)
        return {"error": f"Failed to connect to Supabase: {exc}"}

    # 2. Read the existing profile once: running stats, last hash, tone/register,
    #    and whether the 5-minute LLM throttle is still active.
    prior_row: dict = {}
    try:
        prior = await execute_async(
            client.table(PROFILE_TABLE)
            .select("feature_mean, feature_m2, essay_count, last_content_hash, "
                    "tone, register, updated_at")
            .eq("user_id", user_id)
            .eq("essay_type", persona)
        )
        rows = getattr(prior, "data", None) or []
        if rows:
            prior_row = rows[0] or {}
    except Exception as exc:
        logger.warning(
            "[style_profile] could not read existing profile for %s: %s",
            user_id,
            exc,
        )

    # 3. Tone/register extraction is rate-limited (Groq/OpenRouter): throttle it
    #    and reuse the saved values when recent. The Welford update below is
    #    always allowed, so history builds on every distinct essay.
    llm_fresh = _profile_is_fresh(prior_row)
    metrics: dict = {}
    if not llm_fresh:
        try:
            metrics = await _extract_metrics(stripped)
        except Exception as exc:
            logger.error(
                "[style_profile] metric extraction failed (%s). Using saved/fallback.",
                exc,
            )
    if not metrics:
        metrics = {
            "tone": prior_row.get("tone") or "neutral",
            "register": prior_row.get("register") or "standard",
        }
    else:
        # A transient failure must not wipe a good saved tone/register.
        if not metrics.get("tone"):
            metrics["tone"] = prior_row.get("tone") or "neutral"
        if not metrics.get("register"):
            metrics["register"] = prior_row.get("register") or "standard"

    # 4. Update the running mean/M2/count (Welford) unless this exact essay was
    #    already folded in — clicking Analyze twice on one text must not inflate
    #    the count or bias the mean.
    size = len(sample)
    mean = _as_float_list(prior_row.get("feature_mean"), size) or [0.0] * size
    m2 = _as_float_list(prior_row.get("feature_m2"), size) or [0.0] * size
    count = max(0, int(prior_row.get("essay_count") or 0))
    last_hash = prior_row.get("last_content_hash")

    if last_hash == current_hash:
        logger.info(
            "[style_profile] duplicate essay skipped: user=%s persona=%s essays=%d",
            user_id,
            persona,
            count,
        )
    else:
        mean, m2, count = _welford_update(mean, m2, count, sample)
        last_hash = current_hash

    profile_data = {
        "user_id": user_id,
        "essay_type": persona,
        "tone": _clean_label(metrics.get("tone"), "neutral"),
        "register": _clean_label(metrics.get("register"), "standard"),
        "avg_sentence_length": _as_float(metrics.get("avg_sentence_length"),
                                         sample[0]),
        "vocabulary_richness": _as_float(metrics.get("vocabulary_richness"),
                                         sample[3]),
        "feature_mean": mean,
        "feature_m2": m2,
        "essay_count": count,
        "last_content_hash": last_hash,
        # Without this the 5-minute LLM throttle would never expire: a column
        # DEFAULT only applies on INSERT, not on the upsert's UPDATE branch.
        "updated_at": datetime.now(UTC).isoformat(),
    }

    try:
        response = await execute_async(
            client.table(PROFILE_TABLE).upsert(
                profile_data, on_conflict="user_id,essay_type"
            )
        )
    except Exception as exc:
        logger.exception("[style_profile] Supabase upsert failed: %s", exc)
        return {"error": f"Failed to save style profile: {exc}"}

    rows = len(getattr(response, "data", None) or [])
    logger.info(
        "[style_profile] saved: user=%s persona=%s tone=%s register=%s "
        "essays=%d rows=%d",
        user_id,
        persona,
        profile_data["tone"],
        profile_data["register"],
        count,
        rows,
    )
    return {"success": True, "profile": profile_data}


async def check_style_drift(
    user_id: str,
    current_text: str,
    essay_type: str,
    supabase_client,
) -> dict:
    """Compare the current text against the saved baseline for its persona."""
    persona = normalize_essay_type(essay_type)
    text = (current_text or "").strip()

    try:
        saved_profile = await execute_async(
            supabase_client.table(PROFILE_TABLE)
            .select("feature_mean, feature_m2, essay_count, tone")
            .eq("user_id", user_id)
            .eq("essay_type", persona)
        )
        rows = getattr(saved_profile, "data", None) or []
        if not rows:
            return {"error": "No saved profile for this persona"}
        profile = rows[0] or {}
        saved_tone = profile.get("tone") or "unknown"

        mean = _as_float_list(profile.get("feature_mean"), len(FEATURE_NAMES))
        m2 = _as_float_list(profile.get("feature_m2"), len(FEATURE_NAMES))
        count = max(0, int(profile.get("essay_count") or 0))
        if mean is None or m2 is None or count < MIN_HISTORY_ESSAYS:
            logger.info(
                "[style_drift] not enough history: user=%s persona=%s essays=%d",
                user_id,
                persona,
                count,
            )
            return {
                "is_drastic": False,
                "error": None,
                "reason": "not enough history",
                "changes": [],
                "saved_tone": saved_tone,
            }

        words = len(text.split())
        if words < MIN_DRIFT_WORDS:
            return {
                "is_drastic": False,
                "error": (
                    "Text is too short for a reliable style check "
                    f"(needs at least {MIN_DRIFT_WORDS} words)."
                ),
                "reason": "too short",
                "changes": [],
                "saved_tone": saved_tone,
            }

        sample = await asyncio.to_thread(style_features, text)
        z_scores = []
        for index, value in enumerate(sample):
            variance = m2[index] / (count - 1)
            std = math.sqrt(max(0.0, variance))
            z_scores.append(abs(value - mean[index]) / _effective_std(std, mean[index]))

        breaching = [i for i, z in enumerate(z_scores) if z >= Z_THRESHOLD]
        is_drastic = len(breaching) >= MIN_DRIFT_FEATURES
        top = sorted(breaching, key=lambda i: z_scores[i], reverse=True)[:3]
        changes = [_describe_change(FEATURE_NAMES[i], mean[i], sample[i])
                   for i in top]

        logger.info(
            "[style_drift] result user=%s persona=%s essays=%d breaching=%d "
            "drastic=%s saved_tone=%s",
            user_id,
            persona,
            count,
            len(breaching),
            is_drastic,
            saved_tone,
        )
        return {
            "is_drastic": is_drastic,
            "saved_tone": saved_tone,
            "error": None,
            "changes": changes,
            "z_scores": [round(z, 2) for z in z_scores],
            "threshold": Z_THRESHOLD,
        }
    except Exception as exc:
        logger.exception("[style_drift] failed for %s: %s", user_id, exc)
        return {"error": f"Failed to check style drift: {exc}"}
