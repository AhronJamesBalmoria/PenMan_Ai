"""Layer 2: structural analysis via Gemini."""
from __future__ import annotations

import logging

from app.config import settings
from app.services.gemini import generate_json, load_prompt
from app.services.schemas import StructureReport

logger = logging.getLogger(__name__)

PROMPT_NAME = "structural.txt"
MIN_ESSAY_CHARS = 50
UNAVAILABLE = "Analysis is unavailable right now. Please try again."


async def analyze_structure(
    essay_text: str,
    essay_type: str = "expository",
    user_profile: dict | None = None,
) -> dict:
    """Evaluate introduction/body/conclusion/flow/tone.

    ``user_profile`` (tone/register from ``author_profiles``) is injected into
    the prompt so feedback respects the writer's established voice.

    Returns scores, feedback, a summary, quote-based ``specific_suggestions``,
    the model used, and an error message when the model chain could not produce
    a usable response.
    """
    text = (essay_text or "").strip()
    if len(text) < MIN_ESSAY_CHARS:
        return {
            "error": f"Essay is too short for structural analysis (min {MIN_ESSAY_CHARS} chars).",
            "truncated": False,
        }

    profile = user_profile or {}
    truncated = len(text) > settings.MAX_ESSAY_CHARS
    prompt = load_prompt(
        PROMPT_NAME,
        {
            "essay_type": essay_type,
            "essay_text": text[: settings.MAX_ESSAY_CHARS],
            "user_tone": profile.get("tone") or "Not yet established",
            "user_register": profile.get("register") or "Not yet established",
        },
    )

    data, model_used, error = await generate_json(prompt)
    if error:
        logger.warning("Structural analysis failed: %s", error)
        return {"error": UNAVAILABLE, "truncated": truncated}

    report = StructureReport.model_validate(data)

    return {
        "scores": {
            "introduction": report.introduction.score,
            "body": report.body.score,
            "conclusion": report.conclusion.score,
            "flow": report.flow.score,
            "tone": report.tone.score,
            "overall": report.overall.score,
        },
        "feedback": {
            "introduction": report.introduction.feedback,
            "body": report.body.feedback,
            "conclusion": report.conclusion.feedback,
            "flow": report.flow.feedback,
            "tone": report.tone.feedback,
        },
        "summary": report.overall.summary,
        "specific_suggestions": [
            suggestion.model_dump() for suggestion in report.specific_suggestions
        ],
        "model_used": model_used,
        "truncated": truncated,
        "error": None,
    }
