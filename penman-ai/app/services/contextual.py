"""Layer 2: contextual analysis via Gemini (tone, register, intent)."""
from __future__ import annotations

import logging

from app.config import settings
from app.services.gemini import generate_json, load_prompt
from app.services.schemas import ContextualReport

logger = logging.getLogger(__name__)

PROMPT_NAME = "contextual.txt"
MIN_ESSAY_CHARS = 50
UNAVAILABLE = "Analysis is unavailable right now. Please try again."


async def analyze_contextual(essay_text: str, essay_type: str = "expository") -> dict:
    """Describe tone/register/intent and suggest intent-preserving improvements."""
    text = (essay_text or "").strip()
    if len(text) < MIN_ESSAY_CHARS:
        return {
            "error": f"Essay is too short for contextual analysis (min {MIN_ESSAY_CHARS} chars).",
            "truncated": False,
        }

    truncated = len(text) > settings.MAX_ESSAY_CHARS
    prompt = load_prompt(
        PROMPT_NAME,
        {"essay_type": essay_type, "essay_text": text[: settings.MAX_ESSAY_CHARS]},
    )

    data, model_used, error = await generate_json(prompt)
    if error:
        logger.warning("Contextual analysis failed: %s", error)
        return {"error": UNAVAILABLE, "truncated": truncated}

    report = ContextualReport.model_validate(data)

    return {
        "tone": report.tone or "—",
        "register": report.register_ or "—",
        "intent": report.intent,
        "alignment_score": report.alignment_score,
        "alignment_note": report.alignment_note,
        "suggestions": [
            suggestion.model_dump() for suggestion in report.suggestions
        ],
        "model_used": model_used,
        "truncated": truncated,
        "error": None,
    }
