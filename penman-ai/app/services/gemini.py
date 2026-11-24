"""Shared AI client: Groq/OpenRouter chain and prompt loader."""
from __future__ import annotations

import hashlib
import json
import logging
from collections import OrderedDict
from pathlib import Path

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

# Provider endpoints
GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"

# Prompt directory
PROMPT_DIR = Path(__file__).parent.parent / "prompts"

RETRYABLE_STATUSES = {429, 500, 502, 503, 504}
REQUEST_TIMEOUT = 45.0

# One classroom hitting "Analyze" on the same draft must not burn the Groq /
# OpenRouter quota. Successful JSON answers are memoised per process, keyed by
# a hash of the exact prompt. Entries are evicted oldest-first so a long-lived
# worker cannot grow without bound, and errors are never cached (a 429 should
# be retried on the next click).
CACHE_MAX_ENTRIES = 256
_RESPONSE_CACHE: OrderedDict[str, tuple[dict, str]] = OrderedDict()


def _cache_key(prompt: str, temperature: float) -> str:
    payload = f"{temperature:.4f}\x00{prompt}".encode()
    return hashlib.sha256(payload).hexdigest()


def _cache_get(key: str) -> tuple[dict, str] | None:
    entry = _RESPONSE_CACHE.get(key)
    if entry is not None:
        _RESPONSE_CACHE.move_to_end(key)
    return entry


def _cache_put(key: str, parsed: dict, model_used: str) -> None:
    _RESPONSE_CACHE[key] = (parsed, model_used)
    _RESPONSE_CACHE.move_to_end(key)
    while len(_RESPONSE_CACHE) > CACHE_MAX_ENTRIES:
        _RESPONSE_CACHE.popitem(last=False)


def clear_cache() -> None:
    """Drop every memoised answer (used by tests and manual cache busts)."""
    _RESPONSE_CACHE.clear()

# Groq's JSON mode rejects responses that are not a single valid JSON object.
# A system message plus the explicit "JSON" wording in every prompt is required
# by OpenAI-compatible JSON mode implementations.
JSON_SYSTEM_PROMPT = (
    "You are a JSON-only API. Respond with a single valid JSON object. "
    "Never use markdown, code fences, or commentary. The response must be "
    "valid JSON and nothing else."
)


def load_prompt(name: str, replacements: dict[str, str]) -> str:
    """Load a prompt template and substitute ``{{placeholders}}``."""
    template = (PROMPT_DIR / name).read_text(encoding="utf-8")
    for key, value in replacements.items():
        template = template.replace(f"{{{{{key}}}}}", value)
    return template


def clean_json_text(text: str) -> str:
    """Extract JSON even if the AI wraps it in markdown or extra prose."""
    text = (text or "").strip()

    # Remove a complete fenced block first, e.g. ```json\n{...}\n```.
    if text.startswith("```"):
        parts = text.split("```")
        if len(parts) > 1:
            text = parts[1].strip()
            if text.lower().startswith("json"):
                text = text[4:].lstrip()
            if text.endswith("```"):
                text = text[:-3].strip()

    start_idx = text.find("{")
    end_idx = text.rfind("}")

    if start_idx != -1 and end_idx != -1 and start_idx < end_idx:
        return text[start_idx:end_idx + 1]

    return text


def parse_json_object(text: str) -> dict | None:
    try:
        parsed = json.loads(clean_json_text(text))
    except (json.JSONDecodeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _build_payload(
    provider: str,
    model: str,
    prompt: str,
    temperature: float,
    json_mode: bool,
) -> dict:
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": JSON_SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "temperature": temperature,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    # gpt-oss models are reasoning-first; low effort keeps them from spending
    # the whole completion on hidden reasoning and emitting invalid JSON.
    if provider == "groq" and "gpt-oss" in model.lower():
        payload["reasoning_effort"] = "low"
    return payload


async def _post(url: str, headers: dict, payload: dict) -> httpx.Response:
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        return await client.post(url, headers=headers, json=payload)


async def _call_provider(
    provider: str,
    model: str,
    prompt: str,
    api_key: str,
    temperature: float = 0.0,
    json_mode: bool = True,
) -> tuple[str | None, str | None]:
    url = GROQ_API_URL if provider == "groq" else OPENROUTER_API_URL

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    if provider == "openrouter":
        headers.setdefault("HTTP-Referer", "http://localhost:8000")
        headers.setdefault("X-Title", "Penman AI")

    payload = _build_payload(
        provider, model, prompt, temperature, json_mode)

    try:
        response = await _post(url, headers, payload)
    except httpx.HTTPError as exc:
        return None, f"Network error: {exc}"

    # Some Groq models occasionally fail their own JSON validation even
    # though the prompt asks for JSON. Retry once without response_format and
    # let clean_json_text() recover the object from the model output.
    validation_error = (
        "json_validate_failed" in response.text.lower()
        or "failed to validate json" in response.text.lower()
    )
    if (
        provider == "groq"
        and json_mode
        and response.status_code == 400
        and validation_error
    ):
        logger.warning(
            "[ai] Groq rejected JSON mode for %s; retrying without "
            "response_format",
            model,
        )
        payload.pop("response_format", None)
        try:
            response = await _post(url, headers, payload)
        except httpx.HTTPError as exc:
            return None, f"Network error: {exc}"

    if response.status_code in RETRYABLE_STATUSES:
        return None, f"RETRYABLE:{response.status_code}"

    if response.status_code >= 400:
        logger.warning(
            "[ai] %s %s returned HTTP %d: %s",
            provider,
            model,
            response.status_code,
            ascii(response.text[:300]),
        )
        return None, f"HTTP {response.status_code}: {response.text[:200]}"

    try:
        data = response.json()
        choices = data.get("choices") or []
        if not choices:
            return None, "No choices in response."
        text = choices[0].get("message", {}).get("content", "")
        if not text:
            return None, "Empty response content."
        return text, None
    except ValueError:
        return None, "Response was not JSON."


async def generate_json(
    prompt: str, temperature: float = 0.0
) -> tuple[dict | None, str | None, str | None]:
    """Try each configured provider/model until one returns a JSON object.

    Identical prompts are answered from an in-process cache, so repeated
    analyses of the same draft cost one provider call instead of one per
    click (and per concurrent user).
    """
    key = _cache_key(prompt, temperature)
    cached = _cache_get(key)
    if cached is not None:
        parsed, model_used = cached
        logger.info("[ai] cache hit model=%s", model_used)
        return parsed, model_used, None

    targets: list[tuple[str, str]] = []

    if settings.GROQ_API_KEY:
        targets.append(("groq", settings.GROQ_MODEL))
        fallback = getattr(settings, "GROQ_FALLBACK_MODEL", "")
        if fallback and fallback != settings.GROQ_MODEL:
            targets.append(("groq", fallback))

    if settings.OPENROUTER_API_KEY:
        targets.append(("openrouter", settings.OPENROUTER_MODEL))

    if not targets:
        return None, None, "No API keys configured (Groq/OpenRouter)."

    last_error = "No model configured."

    for provider, model in targets:
        api_key = (
            settings.GROQ_API_KEY
            if provider == "groq"
            else settings.OPENROUTER_API_KEY
        )

        logger.info("[ai] attempting %s with %s", provider, model)
        text, error = await _call_provider(
            provider, model, prompt, api_key, temperature=temperature
        )

        if error:
            last_error = f"{provider} ({model}): {error}"
            logger.warning(
                "[ai] %s %s failed: %s", provider, model, error)
            continue

        parsed = parse_json_object(text or "")
        if parsed is None:
            last_error = (
                f"{provider} ({model}): response was not a JSON object."
            )
            logger.error(
                "[ai] %s returned unparseable JSON: %s",
                provider,
                ascii((text or "")[:500]),
            )
            continue

        logger.info("[ai] success provider=%s model=%s", provider, model)
        model_used = f"{provider}/{model}"
        _cache_put(key, parsed, model_used)
        return parsed, model_used, None

    return None, None, last_error
