"""Human-readable drift-change phrasing."""
import asyncio

from app.routers.analysis import _drift_warning
from app.services.style_profiler import _describe_change


def test_sentence_length_wording():
    text = _describe_change("average sentence length", 20.0, 10.0)
    assert text == "your sentences are about 50% shorter than usual"


def test_zero_baseline_punctuation_wording():
    text = _describe_change("exclamation use", 0.0, 0.05)
    assert text == "you use exclamation marks far more often than usual"


def test_zero_baseline_decrease_wording():
    text = _describe_change("question use", 0.02, 0.0)
    assert text == "you use question marks about 100% less often than usual"


def test_function_word_wording():
    text = _describe_change("however", 0.01, 0.05)
    assert text == '"however" shows up about 400% more often than usual in your writing'


def test_no_change_is_empty():
    assert _describe_change("comma use", 0.05, 0.05) == ""


def test_drift_warning_joins_changes_clearly():
    class FakeQuery:
        def __init__(self, row):
            self.row = row

        def select(self, *a, **k):
            return self

        def eq(self, k, v):
            return self

        def execute(self):
            return type("R", (), {"data": [self.row]})()

    class FakeClient:
        def table(self, name):
            return FakeQuery({
                "feature_mean": [0.0],
                "feature_m2": [0.0],
                "essay_count": 3,
                "tone": "reflective",
            })

    # Monkeypatch check_style_drift at module level.
    import app.routers.analysis as analysis

    async def fake_drift(user_id, text, essay_type, client):
        return {
            "is_drastic": True,
            "saved_tone": "reflective",
            "changes": [
                "your sentences are about 83% shorter than usual",
                "you use exclamation marks far more often than usual",
            ],
            "error": None,
        }

    original = analysis.check_style_drift
    analysis.check_style_drift = fake_drift
    try:
        warning = asyncio.run(
            _drift_warning("u", "word " * 110, "creative", FakeClient(), "structure")
        )
    finally:
        analysis.check_style_drift = original

    assert "Here's what changed:" in warning
    assert "; " in warning
    assert "your sentences are about 83% shorter than usual" in warning
    assert "you use exclamation marks far more often than usual." in warning
