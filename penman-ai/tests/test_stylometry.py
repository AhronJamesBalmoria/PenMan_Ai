"""Stylometric features and style-drift logic (pure Python, no network)."""
import asyncio

import pytest

from app.services.stylometry import FEATURE_COUNT, FEATURE_NAMES, style_features
from app.services.style_profiler import check_style_drift

NORMAL_ESSAY = (
    "Writing by hand feels slow compared with typing. It demands attention. "
    "However, the friction is precisely why it stays memorable.\n\n"
    "When fingers guide a nib across paper, the brain engages in a tactile "
    "choreography that typing cannot replicate. Students often discover that "
    "notes taken by hand are understood more deeply.\n\n"
    "Therefore, although keyboards are efficient, the quiet act of writing "
    "still has a place in learning."
)


def test_style_features_empty_text_is_safe():
    vector = style_features("")
    assert len(vector) == FEATURE_COUNT == len(FEATURE_NAMES)
    assert all(value == 0.0 for value in vector)


def test_style_features_one_word():
    vector = style_features("Hello")
    assert len(vector) == FEATURE_COUNT
    assert vector[2] == pytest.approx(5.0)  # average word length
    assert vector[3] == pytest.approx(1.0)  # vocabulary richness


def test_style_features_one_sentence():
    vector = style_features("The cat sat on the mat.")
    assert len(vector) == FEATURE_COUNT
    assert vector[0] == pytest.approx(6.0)  # sentence length
    assert vector[1] == pytest.approx(0.0)  # no variation


def test_style_features_normal_essay():
    vector = style_features(NORMAL_ESSAY)
    assert len(vector) == FEATURE_COUNT
    assert vector[0] > 5                   # sentence length
    assert 0 < vector[3] <= 1              # MATTR
    assert 0 < vector[4] < 1               # comma rate


def test_style_features_non_ascii():
    vector = style_features("Café naïve — résumé. Très élégant!")
    assert len(vector) == FEATURE_COUNT
    assert all(isinstance(value, float) for value in vector)


class FakeQuery:
    def __init__(self, row):
        self.row = row
        self.filters = {}

    def select(self, *args, **kwargs):
        return self

    def eq(self, key, value):
        self.filters[key] = value
        return self

    def execute(self):
        return type("R", (), {"data": [self.row] if self.row else []})()


class FakeTable:
    def __init__(self, row):
        self.row = row

    def select(self, *args, **kwargs):
        return FakeQuery(self.row)


class FakeClient:
    def __init__(self, row):
        self.row = row

    def table(self, name):
        return FakeTable(self.row)


def test_drift_not_enough_history():
    client = FakeClient({
        "feature_mean": [0.0] * FEATURE_COUNT,
        "feature_m2": [0.0] * FEATURE_COUNT,
        "essay_count": 2,
        "tone": "reflective",
    })
    result = asyncio.run(check_style_drift("u", NORMAL_ESSAY, "expository", client))
    assert result["is_drastic"] is False
    assert result["reason"] == "not enough history"
    assert result["changes"] == []


def test_drift_normal_essay():
    features = style_features(NORMAL_ESSAY)
    client = FakeClient({
        "feature_mean": features,
        "feature_m2": [0.02] * FEATURE_COUNT,
        "essay_count": 10,
        "tone": "reflective",
    })
    result = asyncio.run(check_style_drift("u", NORMAL_ESSAY, "expository", client))
    assert result["is_drastic"] is False
    assert result["error"] is None


def test_drift_clearly_different_essay():
    baseline = style_features(NORMAL_ESSAY)
    other = (
        "Wow! Really? Amazing!! What a day! Is this it? Yes! "
        "Crazy, wild, amazing; unbelievable: fantastic—superb (wow)!"
    )
    other_features = style_features(other)
    m2 = [max(abs(a - b) ** 2 * 0.01, 1e-6)
          for a, b in zip(baseline, other_features, strict=True)]
    client = FakeClient({
        "feature_mean": baseline,
        "feature_m2": m2,
        "essay_count": 10,
        "tone": "reflective",
    })
    result = asyncio.run(check_style_drift("u", other, "expository", client))
    assert result["is_drastic"] is True
    assert len(result["changes"]) <= 3
    assert all(isinstance(change, str) for change in result["changes"])
