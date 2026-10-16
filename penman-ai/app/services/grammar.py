import httpx

LT_API_URL = "https://api.languagetool.org/v2/check"
MAX_TEXT_LENGTH = 20_000  # LanguageTool public API limit


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
        return {
            "matches": [],
            "text_length": len(text),
            "truncated": truncated,
            "error": f"LanguageTool API error: {e}",
        }
    except Exception as e:
        return {
            "matches": [],
            "text_length": len(text),
            "truncated": truncated,
            "error": f"Unexpected error: {e}",
        }