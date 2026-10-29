"""Shared Gemini client: one model chain, one timeout budget, one JSON parser."""
from __future__ import annotations

import json
import logging
import time

import httpx

from app.config import BASE_DIR, settings

logger = logging.getLogger(__name__)

GEMINI_API_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
)

PROMPT_DIR = BASE_DIR / "prompts"

# Statuses where trying another model actually helps.
RETRYABLE_STATUSES = {429, 500, 502, 503, 504}


class GeminiError(Exception):
    """Raised when every model in the chain failed or timed out."""


def load_prompt(name: str, replacements: dict[str, str]) -> str:
    """Load a prompt template and substitute ``{{placeholders}}``."""
    template = (PROMPT_DIR / name).read_text(encoding="utf-8")
    for key, value in replacements.items():
        template = template.replace(f"{{{{{key}}}}}", value)
    return template


def clean_json_text(text: str) -> str:
    """Strip markdown code fences the model may have wrapped the JSON in."""
    text = text.strip()
    if text.startswith("```"):
        parts = text.split("```")
        if len(parts) > 1:
            text = parts[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
        if text.endswith("```"):
            text = text[:-3].strip()
    return text


def parse_json_object(text: str) -> dict | None:
    """Return the parsed JSON object, or None when the response isn't one."""
    try:
        parsed = json.loads(clean_json_text(text))
    except (json.JSONDecodeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


async def _call_gemini(
    model: str, prompt: str, api_key: str, timeout: float
) -> tuple[str | None, str | None]:
    """Call one model. Returns (text, error); retryable statuses get a marker."""
    url = GEMINI_API_URL.format(model=model)
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"responseMimeType": "application/json"},
    }

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(url, params={"key": api_key}, json=payload)
    except httpx.TimeoutException:
        return None, "RETRYABLE:timeout"
    except httpx.HTTPError as exc:
        return None, f"Network error: {exc}"

    if response.status_code in RETRYABLE_STATUSES:
        return None, f"RETRYABLE:{response.status_code}"
    if response.status_code >= 400:
        return None, f"HTTP {response.status_code}: {response.text[:200]}"

    try:
        data = response.json()
    except ValueError:
        return None, "Response was not JSON."

    candidates = data.get("candidates") or []
    if not candidates:
        return None, "No candidates in response."

    parts = (candidates[0].get("content") or {}).get("parts") or []
    text = parts[0].get("text", "") if parts else ""
    if not text:
        return None, "Empty response content."
    return text, None


async def generate_json(prompt: str) -> tuple[dict | None, str | None, str | None]:
    """Try each model until one returns a JSON object.

    Returns ``(data, model_used, error)``. The whole chain shares a single
    deadline so a slow provider can't hold a request open indefinitely.
    """
    if not settings.GEMINI_API_KEY:
        return None, None, "GEMINI_API_KEY is not set."

    deadline = time.monotonic() + settings.GEMINI_TOTAL_TIMEOUT
    last_error = "No model configured."

    for model in settings.GEMINI_MODELS:
        remaining = deadline - time.monotonic()
        if remaining <= 1:
            last_error = "Timed out before a model responded."
            break

        timeout = min(settings.GEMINI_REQUEST_TIMEOUT, remaining)
        text, error = await _call_gemini(model, prompt, settings.GEMINI_API_KEY, timeout)

        if error:
            last_error = f"{model}: {error}"
            logger.warning("Gemini model %s failed: %s", model, error)
            continue

        parsed = parse_json_object(text or "")
        if parsed is None:
            last_error = f"{model}: response was not a JSON object."
            logger.warning("Gemini model %s returned unparseable JSON", model)
            continue

        return parsed, model, None

    return None, None, last_error
