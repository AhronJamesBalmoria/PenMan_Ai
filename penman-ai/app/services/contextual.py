import json
import os
import httpx
from pathlib import Path

GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "contextual.txt"

MODEL_CHAIN = [
    "gemini-3.8-flash",
    "gemini-3.7-flash",
    "gemini-3.6-flash",
]


def _load_prompt(essay_type: str, essay_text: str) -> str:
    template = PROMPT_PATH.read_text(encoding="utf-8")
    return (
        template
        .replace("{{essay_type}}", essay_type)
        .replace("{{essay_text}}", essay_text[:15000])
    )


def _clean_json_text(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
        if text.endswith("```"):
            text = text[:-3].strip()
    return text


async def _call_gemini(model: str, prompt: str, api_key: str):
    url = GEMINI_API_URL.format(model=model)
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"responseMimeType": "application/json"},
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
                return None, "No candidates."
            text = candidates[0].get("content", {}).get("parts", [{}])[0].get("text", "")
            return (text, None) if text else (None, "Empty content.")
    except httpx.HTTPStatusError as e:
        return None, f"HTTP {e.response.status_code}: {e.response.text[:200]}"
    except httpx.HTTPError as e:
        return None, f"Network error: {e}"
    except Exception as e:
        return None, f"Unexpected error: {e}"


async def analyze_contextual(essay_text: str, essay_type: str = "expository") -> dict:
    if not essay_text or len(essay_text.strip()) < 50:
        return {"error": "Essay is too short for contextual analysis (min 50 chars)."}

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return {"error": "GEMINI_API_KEY not set."}

    prompt = _load_prompt(essay_type, essay_text)
    last_error = None

    for model in MODEL_CHAIN:
        text, err = await _call_gemini(model, prompt, api_key)

        if err and err.startswith("RETRYABLE"):
            last_error = f"{model} unavailable, trying next..."
            continue
        if err:
            last_error = f"{model}: {err}"
            continue

        try:
            parsed = json.loads(_clean_json_text(text))
        except json.JSONDecodeError as e:
            last_error = f"{model}: JSON parse failed ({e})"
            continue

        return {
            "tone": parsed.get("tone", "—"),
            "register": parsed.get("register", "—"),
            "intent": parsed.get("intent", ""),
            "alignment_score": parsed.get("alignment_score", 0),
            "alignment_note": parsed.get("alignment_note", ""),
            "suggestions": parsed.get("suggestions", [])[:5],
            "model_used": model,
            "error": None,
        }

    return {"error": f"All models failed. Last error: {last_error}"}