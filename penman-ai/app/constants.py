"""Shared application constants."""

ESSAY_TYPES = [
    "argumentative",
    "expository",
    "narrative",
    "descriptive",
    "persuasive",
    "research",
    "creative",
]

DEFAULT_ESSAY_TYPE = "expository"


def normalize_essay_type(value: str | None) -> str:
    """Return a valid essay type, falling back to the default."""
    candidate = (value or "").strip().lower()
    return candidate if candidate in ESSAY_TYPES else DEFAULT_ESSAY_TYPE
