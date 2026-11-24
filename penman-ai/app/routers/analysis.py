"""Analysis endpoints: grammar, structure, contextual, style profile."""
from __future__ import annotations

import asyncio
import html
import logging
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, BackgroundTasks, Depends, Form, Request
from fastapi.responses import HTMLResponse

from app.config import settings
from app.constants import DEFAULT_ESSAY_TYPE, normalize_essay_type
from app.dependencies import get_current_user, get_user_supabase
from app.services.contextual import analyze_contextual
from app.services.grammar import check_grammar
from app.services.structure import analyze_structure
from app.services.style_profiler import (
    MIN_PROFILE_CHARS,
    check_style_drift,
    update_user_style_profile,
)
from app.templating import templates

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/analyze", tags=["analysis"])

TOO_LONG = (
    f"Essay is too long to analyze. "
    f"Please trim it to {settings.MAX_PLAIN_TEXT_CHARS:,} characters."
)

# Don't re-embed the same author on every click; embeddings are the most
# rate-limited call we make (Gemini 429s), so refreshes are throttled.
PROFILE_REFRESH_INTERVAL = timedelta(minutes=5)

# Drift checks need at least this much text to be meaningful.
MIN_DRIFT_CHARS = 50


async def _load_profile(
    client, user_id: str, essay_type: str
) -> dict | None:
    """Return the saved tone/register for one persona, plus ``updated_at``."""
    try:
        response = (
            client.table("author_profiles")
            .select("tone, register, updated_at")
            .eq("user_id", user_id)
            .eq("essay_type", essay_type)
            .execute()
        )
        if response.data:
            return response.data[0]
        return None
    except Exception:
        # Older deployments may not have the updated_at column yet; the profile
        # itself is still useful, so retry without it and lose only the throttle.
        logger.warning(
            "author_profiles select with updated_at failed; retrying without it",
            exc_info=True,
        )

    try:
        response = (
            client.table("author_profiles")
            .select("tone, register")
            .eq("user_id", user_id)
            .eq("essay_type", essay_type)
            .execute()
        )
        if response.data:
            return response.data[0]
    except Exception:
        logger.warning("Could not load author profile for %s",
                       user_id, exc_info=True)
    return None


def _profile_is_fresh(profile: dict | None) -> bool:
    """True when the stored profile was written inside the throttle window."""
    if not profile:
        return False

    raw = profile.get("updated_at")
    if not raw:
        return False

    try:
        updated = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        logger.warning("Unparseable author_profiles.updated_at: %r", raw)
        return False

    if updated.tzinfo is None:
        updated = updated.replace(tzinfo=UTC)
    return datetime.now(UTC) - updated < PROFILE_REFRESH_INTERVAL


async def _drift_warning(
    user_id: str,
    plain_text: str,
    essay_type: str,
    client,
    route_name: str = "analysis",
) -> str | None:
    """Return a warning when text drifts from the persona's baseline, else None.

    Never raises: a failed drift check (including a missing persona profile)
    must not break the main analysis.
    """
    stripped = (plain_text or "").strip()
    if len(stripped) < MIN_DRIFT_CHARS:
        logger.info(
            "[%s] drift check skipped: %d chars < %d required",
            route_name,
            len(stripped),
            MIN_DRIFT_CHARS,
        )
        return None

    try:
        res = await check_style_drift(
            user_id, plain_text, essay_type, client
        )
    except Exception:
        logger.warning(
            "[%s] style drift check failed for %s (%s)",
            route_name,
            user_id,
            essay_type,
            exc_info=True,
        )
        return None

    if res.get("error"):
        logger.info(
            "[%s] drift check unavailable for %s (%s): %s",
            route_name,
            user_id,
            essay_type,
            res["error"],
        )
        return None

    similarity = res.get("similarity")
    threshold = res.get("threshold")
    is_drastic = bool(res.get("is_drastic"))

    logger.info(
        "[%s] drift result user=%s similarity=%s threshold=%s drastic=%s",
        route_name,
        user_id,
        similarity,
        threshold,
        is_drastic,
    )

    if not is_drastic:
        return None

    saved_tone = res.get("saved_tone") or "saved"
    return (
        f"This looks very different from your usual '{saved_tone}' tone."
    )


def _schedule_profile_update(
    background_tasks: BackgroundTasks,
    user: dict,
    plain_text: str,
    profile: dict | None,
    essay_type: str,
    route_name: str,
) -> None:
    """Queue the style-profiler task, passing the token rather than the client.

    The request-scoped Supabase client is closed once the response is sent, so
    the background task must build its own connection from the access token.
    """
    stripped = (plain_text or "").strip()

    if len(stripped) < MIN_PROFILE_CHARS:
        logger.info(
            "[%s] profile update skipped: %d chars < %d required",
            route_name,
            len(stripped),
            MIN_PROFILE_CHARS,
        )
        return

    if _profile_is_fresh(profile):
        logger.info(
            "[%s] profile update skipped: saved less than %s ago",
            route_name,
            PROFILE_REFRESH_INTERVAL,
        )
        return

    logger.info(
        "[%s] scheduling style profile update: user=%s persona=%s chars=%d",
        route_name,
        user["id"],
        essay_type,
        len(stripped),
    )
    background_tasks.add_task(
        update_user_style_profile,
        user["id"],
        plain_text,
        user["access_token"],
        essay_type,
    )


@router.post("/grammar", response_class=HTMLResponse)
async def analyze_grammar(
    request: Request,
    plain_text: str = Form(""),
    user: dict = Depends(get_current_user),
):
    if len(plain_text) > settings.MAX_PLAIN_TEXT_CHARS:
        return templates.TemplateResponse(
            "partials/grammar_results.html",
            {
                "request": request,
                "matches": [],
                "error": TOO_LONG,
                "truncated": False,
                "count": 0,
            },
        )

    result = await check_grammar(plain_text)

    # Group matches by category for a cleaner UI
    matches = result["matches"]
    for m in matches:
        rule = m.get("rule") or {}
        category = rule.get("category") or {}
        m["category_id"] = category.get("id", "OTHER")
        m["category_name"] = category.get("name", "Other")
        m["issue_type"] = rule.get("issueType", "uncategorized")
        m["replacements"] = [
            r.get("value", "") for r in (m.get("replacements") or [])
        ][:3]

    return templates.TemplateResponse(
        "partials/grammar_results.html",
        {
            "request": request,
            "matches": matches,
            "error": result["error"],
            "truncated": result["truncated"],
            "count": len(matches),
        },
    )


@router.post("/structure", response_class=HTMLResponse)
async def analyze_structure_route(
    request: Request,
    background_tasks: BackgroundTasks,
    plain_text: str = Form(""),
    essay_type: str = Form("expository"),
    user: dict = Depends(get_current_user),
    client=Depends(get_user_supabase),
):
    if len(plain_text) > settings.MAX_PLAIN_TEXT_CHARS:
        return templates.TemplateResponse(
            "partials/structure_results.html",
            {"request": request, "error": TOO_LONG, "truncated": False},
        )

    essay_type = normalize_essay_type(essay_type)

    logger.info(
        "[/api/analyze/structure] reached handler: user=%s persona=%s chars=%d",
        user["id"],
        essay_type,
        len(plain_text.strip()),
    )

    # One read serves both the prompt personalisation and the throttle check.
    profile = await _load_profile(client, user["id"], essay_type)

    logger.info(
        "[/api/analyze/structure] profile loaded: persona=%s tone=%s register=%s",
        essay_type,
        (profile or {}).get("tone"),
        (profile or {}).get("register"),
    )

    # Compare against the persona's saved style BEFORE the background refresh.
    # The drift check and the analysis are independent network calls, so run
    # them together: at 50 concurrent users the saved round-trip matters.
    drift_warning, result = await asyncio.gather(
        _drift_warning(user["id"], plain_text, essay_type, client, "structure"),
        analyze_structure(plain_text, essay_type, profile),
    )

    if drift_warning:
        # Never feed drifted text back into the baseline: otherwise one casual
        # paste would overwrite the author's normal style minutes later.
        logger.info(
            "[/api/analyze/structure] profile update skipped: drift detected"
        )
    else:
        _schedule_profile_update(
            background_tasks,
            user,
            plain_text,
            profile,
            essay_type,
            "structure",
        )

    return templates.TemplateResponse(
        "partials/structure_results.html",
        {"request": request, "drift_warning": drift_warning, **result},
    )


@router.post("/contextual", response_class=HTMLResponse)
async def analyze_contextual_route(
    request: Request,
    background_tasks: BackgroundTasks,
    plain_text: str = Form(""),
    essay_type: str = Form("expository"),
    user: dict = Depends(get_current_user),
    client=Depends(get_user_supabase),
):
    if len(plain_text) > settings.MAX_PLAIN_TEXT_CHARS:
        return templates.TemplateResponse(
            "partials/contextual_results.html",
            {"request": request, "error": TOO_LONG, "truncated": False},
        )

    essay_type = normalize_essay_type(essay_type)

    logger.info(
        "[/api/analyze/contextual] reached handler: user=%s persona=%s chars=%d",
        user["id"],
        essay_type,
        len(plain_text.strip()),
    )

    profile = await _load_profile(client, user["id"], essay_type)

    logger.info(
        "[/api/analyze/contextual] profile loaded: persona=%s tone=%s register=%s",
        essay_type,
        (profile or {}).get("tone"),
        (profile or {}).get("register"),
    )

    # Compare against the persona's saved style BEFORE the background refresh.
    drift_warning, result = await asyncio.gather(
        _drift_warning(user["id"], plain_text, essay_type, client, "contextual"),
        analyze_contextual(plain_text, essay_type, profile),
    )

    if drift_warning:
        logger.info(
            "[/api/analyze/contextual] profile update skipped: drift detected"
        )
    else:
        _schedule_profile_update(
            background_tasks,
            user,
            plain_text,
            profile,
            essay_type,
            "contextual",
        )

    return templates.TemplateResponse(
        "partials/contextual_results.html",
        {"request": request, "drift_warning": drift_warning, **result},
    )


@router.post("/profile", response_class=HTMLResponse)
async def build_profile(
    request: Request,
    plain_text: str = Form(""),
    essay_type: str = Form(DEFAULT_ESSAY_TYPE),
    user: dict = Depends(get_current_user),
):
    """Explicit build: the user asked for it, so the throttle does not apply."""
    if len(plain_text) > settings.MAX_PLAIN_TEXT_CHARS:
        return HTMLResponse(
            f'<div class="alert alert-danger">{html.escape(TOO_LONG)}</div>'
        )

    essay_type = normalize_essay_type(essay_type)
    result = await update_user_style_profile(
        user["id"], plain_text, user["access_token"], essay_type
    )

    if "error" in result:
        return HTMLResponse(
            '<div class="alert alert-danger">'
            f"{html.escape(str(result['error']))}</div>"
        )

    # Labels come from AI output, so they are escaped before being placed in
    # markup; never interpolate model text into an HTMLResponse raw.
    profile = result.get("profile") or {}
    tone = html.escape(str(profile.get("tone") or "neutral").title())
    register = html.escape(str(profile.get("register") or "standard").title())

    return HTMLResponse(
        '<div class="alert alert-success">'
        "<strong>Profile Updated!</strong><br>"
        f"Persona: {html.escape(essay_type.title())}<br>"
        f"Tone: {tone}<br>"
        f"Register: {register}<br>"
        "Vector Memory Saved."
        "</div>"
    )


@router.post("/reset", response_class=HTMLResponse)
async def reset_profile(
    essay_type: str = Form("expository"),
    user: dict = Depends(get_current_user),
    client=Depends(get_user_supabase),
):
    """Delete the saved style baseline for one persona.

    Drift warnings deliberately skip profile updates, so a false positive would
    otherwise freeze the baseline for that persona forever. This endpoint is
    the escape hatch behind the editor's "Reset baseline" button.
    """
    # Normalize essay_type to lowercase to match the database exactly
    essay_type = essay_type.strip().lower() if essay_type else "expository"
    logger.info(
        "[/api/analyze/reset] user=%s persona=%s", user["id"], essay_type
    )

    try:
        res = (
            client.table("author_profiles")
            .delete()
            .eq("user_id", user["id"])
            .eq("essay_type", essay_type)
            .execute()
        )
        removed = len(getattr(res, "data", None) or [])
        logger.info(
            "[/api/analyze/reset] removed %d row(s) for user=%s persona=%s",
            removed,
            user["id"],
            essay_type,
        )
        return HTMLResponse('<span class="text-muted">Baseline reset.</span>')
    except Exception as e:
        logger.exception(
            "[/api/analyze/reset] could not clear baseline for %s", user["id"]
        )
        return HTMLResponse(
            f'<span class="text-danger">Failed to reset: {e}</span>'
        )



@router.post("/style-drift", response_class=HTMLResponse)
async def check_drift_route(
    request: Request,
    plain_text: str = Form(""),
    essay_type: str = Form(DEFAULT_ESSAY_TYPE),
    user: dict = Depends(get_current_user),
    client=Depends(get_user_supabase),
):
    if len(plain_text.strip()) < MIN_DRIFT_CHARS:
        return HTMLResponse(
            '<div class="alert alert-warning">'
            "Text is too short to check style drift.</div>"
        )

    essay_type = normalize_essay_type(essay_type)
    result = await check_style_drift(
        user["id"], plain_text, essay_type, client
    )

    return templates.TemplateResponse(
        "partials/style_drift_results.html",
        {"request": request, **result},
    )
