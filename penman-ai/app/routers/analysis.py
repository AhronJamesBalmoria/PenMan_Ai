"""Analysis endpoints: grammar, structure, contextual, style profile."""
from __future__ import annotations

import asyncio
import html
import logging

from fastapi import APIRouter, BackgroundTasks, Depends, Form, Request
from fastapi.responses import HTMLResponse

from app.config import settings
from app.constants import DEFAULT_ESSAY_TYPE, normalize_essay_type
from app.db.supabase_client import execute_async
from app.dependencies import get_current_user, get_user_supabase
from app.services.contextual import analyze_contextual
from app.services.grammar import check_grammar
from app.services.structure import analyze_structure
from app.services.style_profiler import (
    MIN_DRIFT_WORDS,
    MIN_PROFILE_WORDS,
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


def _word_count(text: str) -> int:
    return len((text or "").split())


async def _load_profile(
    client, user_id: str, essay_type: str
) -> dict | None:
    """Return the saved tone/register for one persona, plus ``updated_at``."""
    try:
        response = await execute_async(
            client.table("author_profiles")
            .select("tone, register, updated_at")
            .eq("user_id", user_id)
            .eq("essay_type", essay_type)
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
        response = await execute_async(
            client.table("author_profiles")
            .select("tone, register")
            .eq("user_id", user_id)
            .eq("essay_type", essay_type)
        )
        if response.data:
            return response.data[0]
    except Exception:
        logger.warning("Could not load author profile for %s",
                       user_id, exc_info=True)
    return None


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
    words = _word_count(stripped)
    if words < MIN_DRIFT_WORDS:
        logger.info(
            "[%s] drift check skipped: %d words < %d required",
            route_name,
            words,
            MIN_DRIFT_WORDS,
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

    is_drastic = bool(res.get("is_drastic"))

    logger.info(
        "[%s] drift result user=%s dramatic=%s threshold=%s changes=%d",
        route_name,
        user_id,
        is_drastic,
        res.get("threshold"),
        len(res.get("changes") or []),
    )

    if not is_drastic:
        return None

    saved_tone = res.get("saved_tone") or "saved"
    changes = [c for c in (res.get("changes") or []) if c]
    warning = f"This looks very different from your usual '{saved_tone}' tone."
    if changes:
        warning += " Here's what changed: " + "; ".join(changes) + "."
    return warning


def _schedule_profile_update(
    background_tasks: BackgroundTasks,
    user: dict,
    plain_text: str,
    essay_type: str,
    route_name: str,
) -> None:
    """Queue the style-profiler task, passing the token rather than the client.

    The request-scoped Supabase client is closed once the response is sent, so
    the background task must build its own connection from the access token.
    """
    stripped = (plain_text or "").strip()
    words = _word_count(stripped)

    if words <= MIN_PROFILE_WORDS:
        logger.info(
            "[%s] profile update skipped: %d words <= %d required",
            route_name,
            words,
            MIN_PROFILE_WORDS,
        )
        return

    # The stylometric update is pure Python and cheap: run it on every distinct
    # essay so history builds in real time. The 5-minute throttle now applies
    # only to the tone/register LLM call inside update_user_style_profile.
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

    # A drift verdict is meaningless when the analysis itself failed. Never show
    # a stale warning alongside an error — the user cannot trust either.
    if result.get("error"):
        drift_warning = None

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

    # A drift verdict is meaningless when the analysis itself failed. Never show
    # a stale warning alongside an error — the user cannot trust either.
    if result.get("error"):
        drift_warning = None

    if drift_warning:
        logger.info(
            "[/api/analyze/contextual] profile update skipped: drift detected"
        )
    else:
        _schedule_profile_update(
            background_tasks,
            user,
            plain_text,
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
        "<strong>Profile updated</strong><br>"
        f"Persona: {html.escape(essay_type.title())}<br>"
        f"Tone: {tone}<br>"
        f"Register: {register}<br>"
        "Stylometric baseline saved."
        "</div>"
    )


@router.post("/accept-drift", response_class=HTMLResponse)
async def accept_drift(
    request: Request,
    plain_text: str = Form(""),
    essay_type: str = Form(DEFAULT_ESSAY_TYPE),
    user: dict = Depends(get_current_user),
):
    """Fold the current essay into the persona's profile instead of resetting.

    Drift detection deliberately skips the normal background profile update,
    so this is the explicit "this is my style" path. Existing history is kept:
    the essay is added as one more Welford sample.
    """
    if len(plain_text) > settings.MAX_PLAIN_TEXT_CHARS:
        return templates.TemplateResponse(
            "partials/baseline_status.html",
            {"request": request, "message": None, "error": TOO_LONG},
        )

    essay_type = normalize_essay_type(essay_type)
    logger.info(
        "[/api/analyze/accept-drift] user=%s persona=%s chars=%d",
        user["id"],
        essay_type,
        len(plain_text),
    )
    result = await update_user_style_profile(
        user["id"], plain_text, user["access_token"], essay_type
    )
    if "error" in result:
        logger.info(
            "[/api/analyze/accept-drift] update failed for %s: %s",
            user["id"],
            result["error"],
        )
        return templates.TemplateResponse(
            "partials/baseline_status.html",
            {"request": request, "message": None, "error": result["error"]},
        )

    return templates.TemplateResponse(
        "partials/baseline_status.html",
        {
            "request": request,
            "message": "Added to your style profile.",
            "error": None,
        },
    )


@router.post("/reset", response_class=HTMLResponse)
async def reset_profile(
    request: Request,
    essay_type: str = Form("expository"),
    user: dict = Depends(get_current_user),
    client=Depends(get_user_supabase),
):
    """Delete the saved style baseline for one persona.

    Drift warnings deliberately skip profile updates, so a false positive would
    otherwise freeze the baseline for that persona forever. This endpoint is
    the escape hatch behind the editor's "Reset baseline" button.
    """
    essay_type = normalize_essay_type(essay_type)
    logger.info(
        "[/api/analyze/reset] user=%s persona=%s", user["id"], essay_type
    )

    try:
        res = await execute_async(
            client.table("author_profiles")
            .delete()
            .eq("user_id", user["id"])
            .eq("essay_type", essay_type)
        )
        removed = len(getattr(res, "data", None) or [])
        logger.info(
            "[/api/analyze/reset] removed %d row(s) for user=%s persona=%s",
            removed,
            user["id"],
            essay_type,
        )
        return templates.TemplateResponse(
            "partials/baseline_status.html",
            {"request": request, "message": "Baseline reset.", "error": None},
        )
    except Exception:
        logger.exception(
            "[/api/analyze/reset] could not clear baseline for %s", user["id"]
        )
        # Never interpolate exception text into markup: it can contain
        # database/URL details, and every other route escapes it.
        return templates.TemplateResponse(
            "partials/baseline_status.html",
            {
                "request": request,
                "message": None,
                "error": "Could not reset the baseline. Please try again.",
            },
        )


@router.post("/style-drift", response_class=HTMLResponse)
async def check_drift_route(
    request: Request,
    plain_text: str = Form(""),
    essay_type: str = Form(DEFAULT_ESSAY_TYPE),
    user: dict = Depends(get_current_user),
    client=Depends(get_user_supabase),
):
    if _word_count(plain_text) < MIN_DRIFT_WORDS:
        return HTMLResponse(
            '<div class="alert alert-warning">'
            f"Text is too short to check style drift (needs at least "
            f"{MIN_DRIFT_WORDS} words).</div>"
        )

    essay_type = normalize_essay_type(essay_type)
    result = await check_style_drift(
        user["id"], plain_text, essay_type, client
    )

    return templates.TemplateResponse(
        "partials/style_drift_results.html",
        {"request": request, **result},
    )
