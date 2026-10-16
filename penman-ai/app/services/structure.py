import json
import os
import httpx
from pathlib import Path

GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "structural.txt"

# Fallback chain — tries in order until one succeeds.
# Gemini 3.8 is newest but can return 503 during launch spikes.
MODEL_CHAIN = [
    "gemini-3.8-flash",   # primary  — newest, smartest
    "gemini-3.7-flash",   # fallback — stable
    "gemini-3.6-flash",   # fallback — proven, reliable
]


def _load_prompt(essay_type: str, essay_text: str) -> str:
    template = PROMPT_PATH.read_text(encoding="utf-8")
    return (
        template
        .replace("{{essay_type}}", essay_type)
        .replace("{{essay_text}}", essay_text[:15000])
    )


def _clean_json_text(text: str) -> str:
    """Strip markdown code fences if Gemini wrapped the JSON."""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
        if text.endswith("```"):
            text = text[:-3].strip()
    return text


async def _call_gemini(model: str, prompt: str, api_key: str) -> tuple[str | None, str | None]:
    """
    Try a single Gemini model. Returns (text, error).
    Treats 503/429 as retryable — caller should try next model.
    """
    url = GEMINI_API_URL.format(model=model)
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            # Gemini 3.x does NOT accept temperature/top_p/top_k.
            # Only responseMimeType is valid.
            "responseMimeType": "application/json",
        },
    }

    try:
        async with httpx.AsyncClient(timeout=45) as client:
            r = await client.post(url, params={"key": api_key}, json=payload)

            if r.status_code in (503, 429):
                return None, f"RETRYABLE:{r.status_code}"

            r.raise_for_status()
            data = r.json()

            candidates = data.get("candidates", [])
            if not candidates:
                return None, "No candidates in response."

            text = candidates[0].get("content", {}).get("parts", [{}])[0].get("text", "")
            if not text:
                return None, "Empty response content."

            return text, None

    except httpx.HTTPStatusError as e:
        return None, f"HTTP {e.response.status_code}: {e.response.text[:200]}"
    except httpx.HTTPError as e:
        return None, f"Network error: {e}"
    except Exception as e:
        return None, f"Unexpected error: {e}"


async def analyze_structure(essay_text: str, essay_type: str = "expository") -> dict:
    """
    Try each model in MODEL_CHAIN until one returns valid JSON.
    Returns:
        {
          "scores": {...},
          "feedback": {...},
          "summary": str,
          "model_used": str,
          "error": str | None
        }
    """
    if not essay_text or len(essay_text.strip()) < 50:
        return {"error": "Essay is too short for structural analysis (min 50 chars)."}

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return {"error": "GEMINI_API_KEY not set."}

    prompt = _load_prompt(essay_type, essay_text)
    last_error = None

    for model in MODEL_CHAIN:
        text, err = await _call_gemini(model, prompt, api_key)

        if err and err.startswith("RETRYABLE"):
            last_error = f"{model} unavailable ({err.split(':')[1]}), trying next model..."
            continue

        if err:
            last_error = f"{model}: {err}"
            continue

        # Got text — try to parse JSON
        try:
            parsed = json.loads(_clean_json_text(text))
        except json.JSONDecodeError as e:
            last_error = f"{model}: JSON parse failed ({e})"
            continue

        # Success
        return {
            "scores": {
                "introduction": parsed.get("introduction", {}).get("score"),
                "body": parsed.get("body", {}).get("score"),
                "conclusion": parsed.get("conclusion", {}).get("score"),
                "flow": parsed.get("flow", {}).get("score"),
                "tone": parsed.get("tone", {}).get("score"),
                "overall": parsed.get("overall", {}).get("score"),
            },
            "feedback": {
                "introduction": parsed.get("introduction", {}).get("feedback", ""),
                "body": parsed.get("body", {}).get("feedback", ""),
                "conclusion": parsed.get("conclusion", {}).get("feedback", ""),
                "flow": parsed.get("flow", {}).get("feedback", ""),
                "tone": parsed.get("tone", {}).get("feedback", ""),
            },
            "summary": parsed.get("overall", {}).get("summary", ""),
            "model_used": model,
            "error": None,
        }

    # Every model in the chain failed
    return {"error": f"All models failed. Last error: {last_error}"}
