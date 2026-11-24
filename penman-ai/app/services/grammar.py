import asyncio
import logging

import httpx

logger = logging.getLogger(__name__)

LT_API_URL = "https://api.languagetool.org/v2/check"
MAX_TEXT_LENGTH = 20_000  # LanguageTool public API limit
FRIENDLY_ERROR = "Grammar check is temporarily unavailable. Please try again in a moment."


async def check_grammar(text: str, language: str = "en-US") -> dict:
    """
    Send text to LanguageTool public API and return parsed matches.

    Returns:
        {
          "matches": [ {message, shortMessage, offset, length,
                        context, replacements, rule, type}, ... ],
          "text_length": int,
          "truncated": bool,
          "error": str | None
        }
    """
    if not text or not text.strip():
        return {"matches": [], "text_length": 0, "truncated": False, "error": None}

    truncated = False
    if len(text) > MAX_TEXT_LENGTH:
        text = text[:MAX_TEXT_LENGTH]
        truncated = True

    last_error = None
    for attempt in range(2):
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.post(
                    LT_API_URL,
                    data={
                        "text": text,
                        "language": language,
                        "enabledOnly": "false",
                    },
                )
                response.raise_for_status()
                data = response.json()
                return {
                    "matches": data.get("matches", []),
                    "text_length": len(text),
                    "truncated": truncated,
                    "error": None,
                }
        except httpx.HTTPError as e:
            last_error = e
            logger.warning("LanguageTool request failed (attempt %d): %s", attempt + 1, e)
            if attempt == 0:
                await asyncio.sleep(1)
        except Exception as e:
            last_error = e
            logger.exception("Unexpected error calling LanguageTool")

    # Log the real failure server-side, but return a clean message to the UI.
    logger.error("LanguageTool check unavailable: %s", last_error)
    return {
        "matches": [],
        "text_length": len(text),
        "truncated": truncated,
        "error": FRIENDLY_ERROR,
    }
