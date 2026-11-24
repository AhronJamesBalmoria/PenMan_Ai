"""Adaptive feedback: profile an author's style locally with FastEmbed.

The 384-dimension style vector is produced by BAAI/bge-small-en-v1.5 running on
ONNX Runtime (~100 MB RSS), not PyTorch (~2 GB). That difference is what lets
the app run on a free-tier 512 MB cloud instance.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import threading
from typing import Any

from fastembed import TextEmbedding

from app.config import settings
from app.constants import DEFAULT_ESSAY_TYPE, normalize_essay_type
from app.db.supabase_client import get_supabase_with_token

# Uses the Groq/OpenRouter fallback chain
from app.services.gemini import generate_json

logger = logging.getLogger(__name__)

# BGE-small uses 384 dimensions.
EMBEDDING_DIMENSIONS = 384
EMBEDDING_MODEL_NAME = "BAAI/bge-small-en-v1.5"
MIN_PROFILE_CHARS = 100
MAX_TEXT_CHARS = 20_000

# BGE is sensitive to topic as well as style. The threshold is intentionally
# sensitive to content changes; override it with STYLE_DRIFT_THRESHOLD when tuning.
DRIFT_THRESHOLD = settings.STYLE_DRIFT_THRESHOLD


class StyleProfileError(RuntimeError):
    pass


# The model is created on first use, not at import time: it costs ~130 MB of
# disk on first download and is pointless work for processes that never embed
# (tests, CLI helpers). The lock keeps two concurrent first requests from
# loading the model twice.
_embedder: TextEmbedding | None = None
_embedder_lock = threading.Lock()


def _get_embedder() -> TextEmbedding:
    """Return the process-wide FastEmbed model, building it once (thread-safe)."""
    global _embedder
    if _embedder is not None:
        return _embedder

    with _embedder_lock:
        if _embedder is None:
            logger.info(
                "Loading FastEmbed model (%s)...", EMBEDDING_MODEL_NAME)
            _embedder = TextEmbedding(model_name=EMBEDDING_MODEL_NAME)
            logger.info("FastEmbed model loaded.")
    return _embedder


def warm_up_embedder() -> bool:
    """Load the model ahead of the first request. Never raises."""
    try:
        _get_embedder()
        return True
    except Exception:
        logger.warning(
            "Could not warm up the embedding model; the first analysis will "
            "retry.",
            exc_info=True,
        )
        return False


def _as_float(value, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _clean_label(value, default: str) -> str:
    text = str(value or "").strip().lower()
    return text[:40] or default


def _vector_literal(values: list[float]) -> str:
    """Serialize floats as pgvector's text literal, e.g. ``[0.1,-0.2]``."""
    return "[" + ",".join(str(float(v)) for v in values) + "]"


def _parse_embedding(value: Any) -> list[float]:
    """Normalise a pgvector value returned by PostgREST into ``list[float]``.

    Supabase can return the column as a JSON-ish string (``"[0.1,0.2]"``), a
    Postgres array string (``"{0.1,0.2}"``), or an already-decoded list. Older
    deployments even stored 3072-dimension Gemini vectors, so callers must
    check the length before doing any math.
    """
    if value is None:
        raise ValueError("embedding is None")

    if isinstance(value, str):
        text = value.strip()
        if not text:
            raise ValueError("embedding string is empty")
        try:
            value = json.loads(text)
        except (json.JSONDecodeError, ValueError):
            # Postgres array literal: {0.1,0.2,...}
            parts = text.strip("{}[]").split(",")
            value = [part for part in parts if part.strip()]

    if isinstance(value, dict):
        for key in ("embedding", "style_embedding", "vector", "values"):
            if key in value:
                return _parse_embedding(value[key])
        raise ValueError("embedding object has no vector field")

    if not isinstance(value, (list, tuple)):
        raise ValueError(
            f"unsupported embedding type: {type(value).__name__}"
        )

    try:
        values = [float(item) for item in value]
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"embedding contains non-numeric values: {exc}") from exc

    if any(not math.isfinite(item) for item in values):
        raise ValueError("embedding contains non-finite values")
    return values


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


def _generate_embedding(text: str) -> list[float]:
    """Generate the 384-dimension vector LOCALLY with FastEmbed (ONNX).

    This function blocks: callers running on the event loop must use
    :func:`_generate_embedding_async` so one long essay cannot stall every
    other request.
    """
    stripped = (text or "").strip()
    if not stripped:
        raise StyleProfileError("Cannot embed empty text.")

    try:
        batch = list(_get_embedder().embed([stripped[:MAX_TEXT_CHARS]]))
    except Exception as exc:
        raise StyleProfileError(f"Local embedding failed: {exc}") from exc

    if not batch:
        raise StyleProfileError("Embedding model returned no vectors.")

    encoded = batch[0]
    values = encoded.tolist() if hasattr(encoded, "tolist") else list(encoded)

    if len(values) != EMBEDDING_DIMENSIONS:
        raise StyleProfileError(
            f"Embedding model returned {len(values)} dimensions; "
            f"expected {EMBEDDING_DIMENSIONS}."
        )
    return values


async def _generate_embedding_async(text: str) -> list[float]:
    """Run the CPU-bound ONNX inference in a worker thread."""
    return await asyncio.to_thread(_generate_embedding, text)


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

    if len(stripped) < MIN_PROFILE_CHARS:
        logger.info(
            "[style_profile] skipped: %d chars < %d required",
            len(stripped),
            MIN_PROFILE_CHARS,
        )
        return {"error": "Text too short to extract a reliable style profile."}

    # 1. Try to get metrics (Tone/Register). If it fails, use fallbacks.
    metrics_failed = False
    try:
        metrics = await _extract_metrics(stripped)
    except Exception as exc:
        logger.error(
            "[style_profile] metric extraction failed (%s). Using fallback "
            "values.",
            exc,
        )
        metrics_failed = True
        metrics = {
            "tone": "neutral",
            "register": "standard",
            "avg_sentence_length": 15.0,
            "vocabulary_richness": 0.5,
        }

    # 2. Generate Local Embedding (this should always succeed; it is local).
    try:
        embedding = await _generate_embedding_async(stripped)
        logger.info(
            "[style_profile] local embedding ready: %d dimensions",
            len(embedding),
        )
    except Exception as exc:
        logger.exception("[style_profile] local embedding failed: %s", exc)
        return {"error": f"Could not generate embedding: {exc}"}

    try:
        client = get_supabase_with_token(access_token)
    except Exception as exc:
        logger.exception("[style_profile] Supabase client failed: %s", exc)
        return {"error": f"Failed to connect to Supabase: {exc}"}

    # Keep an existing tone/register if the AI chain is temporarily down; a
    # transient 429 must not overwrite a good profile with neutral/standard.
    if metrics_failed:
        try:
            response = (
                client.table("author_profiles")
                .select("tone, register")
                .eq("user_id", user_id)
                .eq("essay_type", persona)
                .execute()
            )
            if response.data:
                existing = response.data[0] or {}
                metrics["tone"] = existing.get("tone") or metrics["tone"]
                metrics["register"] = (
                    existing.get("register") or metrics["register"]
                )
        except Exception as exc:
            logger.warning(
                "[style_profile] could not read existing profile for %s: %s",
                user_id,
                exc,
            )
        logger.info(
            "[style_profile] fallback tone=%s register=%s",
            metrics["tone"],
            metrics["register"],
        )

    # 3. Save to Database. The composite key keeps one baseline per persona.
    profile_data = {
        "user_id": user_id,
        "essay_type": persona,
        "tone": _clean_label(metrics.get("tone"), "neutral"),
        "register": _clean_label(metrics.get("register"), "standard"),
        "avg_sentence_length": _as_float(
            metrics.get("avg_sentence_length"), 15.0),
        "vocabulary_richness": _as_float(
            metrics.get("vocabulary_richness"), 0.5),
        "style_embedding": _vector_literal(embedding),
    }

    try:
        response = client.table("author_profiles").upsert(
            profile_data, on_conflict="user_id,essay_type"
        ).execute()
    except Exception as exc:
        logger.exception("[style_profile] Supabase upsert failed: %s", exc)
        return {"error": f"Failed to save style profile: {exc}"}

    rows = len(getattr(response, "data", None) or [])
    logger.info(
        "[style_profile] saved: user=%s persona=%s tone=%s register=%s rows=%d",
        user_id,
        persona,
        profile_data["tone"],
        profile_data["register"],
        rows,
    )
    return {"success": True, "profile": profile_data}


def _cosine_similarity(vec1: list[float], vec2: list[float]) -> float:
    """Calculate the cosine similarity between two equal-length vectors."""
    if len(vec1) != len(vec2):
        raise ValueError(
            f"vector dimensions differ: {len(vec1)} vs {len(vec2)}")
    if not vec1:
        raise ValueError("vectors are empty")

    dot_product = math.fsum(a * b for a, b in zip(vec1, vec2, strict=True))
    magnitude1 = math.sqrt(math.fsum(a * a for a in vec1))
    magnitude2 = math.sqrt(math.fsum(b * b for b in vec2))
    if magnitude1 == 0 or magnitude2 == 0:
        return 0.0
    return dot_product / (magnitude1 * magnitude2)


async def check_style_drift(
    user_id: str,
    current_text: str,
    essay_type: str,
    supabase_client,
) -> dict:
    """Compare the current text against the saved baseline for its persona."""
    persona = normalize_essay_type(essay_type)
    text = (current_text or "").strip()
    logger.info(
        "[style_drift] start user=%s persona=%s chars=%d threshold=%.2f",
        user_id,
        persona,
        len(text),
        DRIFT_THRESHOLD,
    )

    try:
        saved_profile = (
            supabase_client.table("author_profiles")
            .select("style_embedding, tone")
            .eq("user_id", user_id)
            .eq("essay_type", persona)
            .execute()
        )
        rows = getattr(saved_profile, "data", None) or []
        logger.info("[style_drift] query returned %d row(s)", len(rows))

        if not rows:
            logger.info(
                "[style_drift] skipped: no profile for user=%s persona=%s",
                user_id,
                persona,
            )
            return {"error": "No saved profile for this persona"}

        if not rows[0].get("style_embedding"):
            logger.info(
                "[style_drift] skipped: no embedding for user=%s persona=%s",
                user_id,
                persona,
            )
            return {"error": "No saved style embedding for this persona."}

        profile = rows[0]
        raw_embedding = profile["style_embedding"]
        saved_tone = profile.get("tone") or "unknown"

        logger.info(
            "[style_drift] stored embedding type=%s",
            type(raw_embedding).__name__,
        )
        try:
            saved_embedding = _parse_embedding(raw_embedding)
        except ValueError as exc:
            logger.warning(
                "[style_drift] could not parse stored embedding for %s: %s",
                user_id,
                exc,
            )
            return {"error": f"Could not parse saved style embedding: {exc}"}

        logger.info(
            "[style_drift] saved embedding dim=%d", len(saved_embedding))

        if len(saved_embedding) != EMBEDDING_DIMENSIONS:
            message = (
                f"Saved style embedding has {len(saved_embedding)} dimensions; "
                f"expected {EMBEDDING_DIMENSIONS}. Please rebuild the profile."
            )
            logger.warning("[style_drift] %s (user=%s)", message, user_id)
            return {
                "error": message,
                "needs_rebuild": True,
                "saved_tone": saved_tone,
            }

        new_embedding = await _generate_embedding_async(text)
        logger.info(
            "[style_drift] current embedding dim=%d", len(new_embedding))

        similarity = _cosine_similarity(saved_embedding, new_embedding)
        is_drastic = similarity < DRIFT_THRESHOLD

        logger.info(
            "[style_drift] result user=%s persona=%s similarity=%.4f "
            "threshold=%.2f drastic=%s saved_tone=%s",
            user_id,
            persona,
            similarity,
            DRIFT_THRESHOLD,
            is_drastic,
            saved_tone,
        )

        return {
            "similarity": round(similarity, 4),
            "similarity_raw": similarity,
            "threshold": DRIFT_THRESHOLD,
            "is_drastic": is_drastic,
            "saved_tone": saved_tone,
            "error": None,
        }

    except Exception as exc:
        logger.exception("[style_drift] failed for %s: %s", user_id, exc)
        return {"error": f"Failed to check style drift: {exc}"}
