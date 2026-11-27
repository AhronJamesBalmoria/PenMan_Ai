"""Tests for Option B: cheap Welford updates + throttled LLM extraction."""
import asyncio
from datetime import UTC, datetime

from app.services import style_profiler
from app.services.stylometry import FEATURE_COUNT

ESSAY = "This is a moderately long essay. " * 20
OTHER = "A completely different sort of text, with other words! " * 20


class FakeQuery:
    def __init__(self, store, table):
        self.store, self.table_name = store, table
        self.filters = {}

    def select(self, *args, **kwargs):
        return self

    def eq(self, key, value):
        self.filters[key] = value
        return self

    def execute(self):
        rows = [
            row for row in self.store[self.table_name].values()
            if all(row.get(k) == v for k, v in self.filters.items())
        ]
        return type("R", (), {"data": rows})()


class FakeTable:
    def __init__(self, store, name):
        self.store, self.name = store, name

    def select(self, *args, **kwargs):
        return FakeQuery(self.store, self.name)

    def upsert(self, payload, on_conflict=None):
        key = (payload.get("user_id"), payload.get("essay_type"))
        row = dict(payload)
        row["id"] = str(key)
        row["updated_at"] = datetime.now(UTC).isoformat()
        self.store[self.name][key] = row
        result = type("R", (), {"data": [row]})()
        return type("U", (), {"execute": lambda self: result})()


class FakeClient:
    def __init__(self):
        self.store = {"author_profiles": {}}

    def table(self, name):
        return FakeTable(self.store, name)


def _setup(monkeypatch, client, metrics_calls=None):
    """Patch the profiler deps; return a list of metric-call texts."""
    calls = metrics_calls if metrics_calls is not None else []

    async def fake_metrics(text):
        calls.append(text)
        return {"tone": "formal", "register": "academic",
                "avg_sentence_length": 12, "vocabulary_richness": 0.6}

    monkeypatch.setattr(style_profiler, "_extract_metrics", fake_metrics)
    monkeypatch.setattr(style_profiler, "get_supabase_with_token", lambda t: client)
    return calls


def test_profile_requires_more_than_100_words(monkeypatch):
    """Exactly 100 words is skipped; 101 words is stored."""
    client = FakeClient()
    _setup(monkeypatch, client)

    too_short = asyncio.run(
        style_profiler.update_user_style_profile(
            "u", "word " * 100, "t", "expository"
        )
    )
    assert "error" in too_short
    assert client.store["author_profiles"] == {}

    accepted = asyncio.run(
        style_profiler.update_user_style_profile(
            "u", "word " * 101, "t", "expository"
        )
    )
    assert accepted["success"] is True
    assert len(client.store["author_profiles"]) == 1


def test_same_essay_twice_does_not_increment_count(monkeypatch):
    client = FakeClient()
    _setup(monkeypatch, client)
    for _ in range(2):
        result = asyncio.run(style_profiler.update_user_style_profile(
            "u", ESSAY, "t", "expository"))
        assert result["success"] is True
    row = next(iter(client.store["author_profiles"].values()))
    assert row["essay_count"] == 1


def test_three_distinct_essays_build_history(monkeypatch):
    client = FakeClient()
    _setup(monkeypatch, client)
    essays = [
        "First essay with several words and a few sentences. " * 15,
        "Second essay, with commas; and other punctuation! " * 15,
        "Third essay? Yes, indeed. More words here. " * 15,
    ]
    for essay in essays:
        asyncio.run(style_profiler.update_user_style_profile(
            "u", essay, "t", "expository"))
    row = next(iter(client.store["author_profiles"].values()))
    assert row["essay_count"] == 3
    assert len(row["feature_mean"]) == FEATURE_COUNT
    assert len(row["feature_m2"]) == FEATURE_COUNT


def test_throttled_llm_reuses_saved_tone(monkeypatch):
    client = FakeClient()
    calls = _setup(monkeypatch, client)
    # First call stores a recent updated_at; second call should skip the LLM.
    asyncio.run(style_profiler.update_user_style_profile(
        "u", ESSAY, "t", "expository"))
    asyncio.run(style_profiler.update_user_style_profile(
        "u", OTHER, "t", "expository"))
    assert len(calls) == 1
    row = next(iter(client.store["author_profiles"].values()))
    assert row["tone"] == "formal"
    assert row["essay_count"] == 2  # Welford still updated
