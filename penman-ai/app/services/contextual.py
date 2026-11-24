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


async def analyze_contextual(
    essay_text: str,
    essay_type: str = "expository",
    user_profile: dict | None = None  # <-- NEW ARGUMENT
) -> dict:
    """Describe tone/register/intent and suggest intent-preserving improvements."""
    text = (essay_text or "").strip()
    if len(text) < MIN_ESSAY_CHARS:
        return {
            "error": f"Essay is too short for contextual analysis (min {MIN_ESSAY_CHARS} chars).",
            "truncated": False,
        }

    # <-- Extract profile data (or use defaults)
    profile = user_profile or {}
    user_tone = profile.get("tone", "Not yet established")
    user_register = profile.get("register", "Not yet established")

    truncated = len(text) > settings.MAX_ESSAY_CHARS

    # <-- Inject profile into the prompt replacements
    prompt = load_prompt(
        PROMPT_NAME,
        {
            "essay_type": essay_type,
            "essay_text": text[: settings.MAX_ESSAY_CHARS],
            "user_tone": user_tone,          # NEW
            "user_register": user_register   # NEW
        },
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
