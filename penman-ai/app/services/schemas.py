"""Validated shapes for the JSON our AI providers return.

Models come back loosely typed (scores as strings, sections missing, lists
where objects belong), so everything is coerced into a predictable shape
before it reaches a template.
"""
from __future__ import annotations

from typing import Annotated, Any

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, model_validator


def _coerce_score(value: Any) -> int | None:
    """Accept 4, "4", "4/5", 4.5, 7 or "high" and return 1-5 or None."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, str):
        value = value.strip().split("/")[0]
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:  # NaN
        return None
    return max(1, min(5, int(round(number))))


def _text(max_length: int):
    def coerce(value: Any) -> str:
        if value is None:
            return ""
        if not isinstance(value, str):
            return ""
        return value.strip()[:max_length]

    return coerce


def _coerce_impact(value: Any) -> int:
    """Accept 82, "82", or "82/100" and return a 0-100 impact score."""
    if value is None or isinstance(value, bool):
        return 0
    if isinstance(value, str):
        value = value.strip().split("/")[0]
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0
    if number != number:  # NaN
        return 0
    return max(0, min(100, int(round(number))))


ShortText = Annotated[str, BeforeValidator(_text(120))]
LongText = Annotated[str, BeforeValidator(_text(1200))]
Score = Annotated[int | None, BeforeValidator(_coerce_score)]
ImpactScore = Annotated[int, BeforeValidator(_coerce_impact)]


class _Model(BaseModel):
    model_config = ConfigDict(extra="ignore")


class Section(_Model):
    score: Score = None
    feedback: LongText = ""


class Overall(_Model):
    score: Score = None
    summary: LongText = ""


class SuggestionItem(_Model):
    """A concrete, quote-based edit suggested by the structural analysis."""

    issue_type: ShortText = ""
    original_text: LongText = ""
    suggestion: LongText = ""
    reason: LongText = ""
    score: ImpactScore = 0


# Backwards-compatible alias for any callers using the earlier name.
SpecificSuggestion = SuggestionItem


class StructureReport(_Model):
    introduction: Section = Field(default_factory=Section)
    body: Section = Field(default_factory=Section)
    conclusion: Section = Field(default_factory=Section)
    flow: Section = Field(default_factory=Section)
    tone: Section = Field(default_factory=Section)
    overall: Overall = Field(default_factory=Overall)
    specific_suggestions: list[SuggestionItem] = Field(
        default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _shape(cls, data: Any) -> dict:
        if not isinstance(data, dict):
            return {}
        shaped = {}
        for key in ("introduction", "body", "conclusion", "flow", "tone", "overall"):
            value = data.get(key)
            shaped[key] = value if isinstance(value, dict) else {}

        suggestions = data.get("specific_suggestions")
        if not isinstance(suggestions, list):
            suggestions = []
        shaped["specific_suggestions"] = [
            item for item in suggestions if isinstance(item, dict)
        ][:5]
        return shaped


PRIORITIES = ("high", "medium", "low")


class Suggestion(_Model):
    category: ShortText = ""
    original_text: LongText = ""
    issue: LongText = ""
    recommendation: LongText = ""
    priority: ShortText = "low"
    score: ImpactScore = 0

    @model_validator(mode="after")
    def _normalize_priority(self):
        priority = self.priority.lower()
        self.priority = priority if priority in PRIORITIES else "low"
        return self


class ContextualReport(_Model):
    tone: ShortText = "—"
    # "register" shadows a BaseModel attribute, so keep it as an input alias.
    register_: ShortText = Field(default="—", validation_alias="register")
    intent: LongText = ""
    alignment_score: Score = None
    alignment_note: LongText = ""
    suggestions: list[Suggestion] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _shape(cls, data: Any) -> dict:
        if not isinstance(data, dict):
            return {}
        suggestions = data.get("suggestions")
        if not isinstance(suggestions, list):
            suggestions = []
        return {
            "tone": data.get("tone"),
            "register": data.get("register"),
            "intent": data.get("intent"),
            "alignment_score": data.get("alignment_score"),
            "alignment_note": data.get("alignment_note"),
            "suggestions": [s for s in suggestions if isinstance(s, dict)][:5],
        }
