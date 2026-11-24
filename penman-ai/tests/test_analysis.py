"""Analysis routes and partials must degrade gracefully."""
import asyncio
import html
import json
from datetime import UTC, datetime, timedelta

import pytest
from conftest import USER_ID

from app.services import structure as structure_service
from app.templating import templates


@pytest.fixture
def scheduled_profile_updates(monkeypatch):
    """Capture profile-update tasks instead of calling Gemini/Supabase."""
    calls = []

    async def fake_update(user_id, text, access_token, essay_type):
        calls.append(
            {
                "user_id": user_id,
                "text": text,
                "access_token": access_token,
                "essay_type": essay_type,
            }
        )
        return {"success": True, "profile": {"tone": "neutral", "register": "standard"}}

    monkeypatch.setattr("app.routers.analysis.update_user_style_profile", fake_update)
    return calls


def _render(name, **context):
    return templates.env.get_template(name).render(**context)


def test_structure_partial_handles_missing_scores():
    rendered = _render(
        "partials/structure_results.html",
        error=None,
        scores={"introduction": None, "body": None, "conclusion": None,
                "flow": None, "tone": None, "overall": None},
        feedback={},
        summary="",
        model_used="gemini-x",
        truncated=False,
    )
    assert "—/5" in rendered
    assert "No feedback returned" in rendered


def test_structure_partial_handles_absent_score_keys():
    rendered = _render(
        "partials/structure_results.html",
        error=None,
        scores={},
        feedback={},
        summary="",
        model_used="gemini-x",
        truncated=False,
    )
    assert "Structural Analysis" in rendered


def test_contextual_partial_handles_missing_alignment():
    rendered = _render(
        "partials/contextual_results.html",
        error=None,
        tone="—",
        register="—",
        intent="",
        alignment_score=None,
        alignment_note="",
        suggestions=[],
        model_used="gemini-x",
        truncated=False,
    )
    assert "Alignment Score" in rendered
    assert "—" in rendered
    assert "/5" in rendered
    assert "No contextual suggestions" in rendered


def test_contextual_partial_escapes_model_output():
    rendered = _render(
        "partials/contextual_results.html",
        error=None,
        tone="<script>alert(1)</script>",
        register="formal",
        intent="to persuade",
        alignment_score=4,
        alignment_note="",
        suggestions=[],
        model_used="gemini-x",
        truncated=False,
    )
    assert "<script>" not in rendered
    assert html.escape("<script>alert(1)</script>") in rendered


def test_contextual_partial_renders_premium_layout():
    rendered = _render(
        "partials/contextual_results.html",
        error=None,
        tone="Formal",
        register="Informative",
        intent="To persuade the reader that media literacy matters.",
        alignment_score=4,
        alignment_note="The essay effectively fulfills its persuasive purpose.",
        model_used="groq/openai/gpt-oss-20b",
        truncated=False,
        suggestions=[
            {
                "category": "Tone",
                "issue": "Some sentences sound conversational.",
                "recommendation": "Use more formal phrasing in the opening.",
                "priority": "high",
            },
            {
                "category": "Evidence",
                "issue": "Claims need support.",
                "recommendation": "Add a statistic from a credible source.",
                "priority": "medium",
            },
            {
                "category": "Register",
                "issue": "Minor shift in register.",
                "recommendation": "Keep terminology consistent.",
                "priority": "low",
            },
        ],
    )

    assert "Contextual Analysis" in rendered
    assert ">4<" in rendered
    assert "/5" in rendered
    assert "Alignment Score" in rendered
    assert "Tone:" in rendered
    assert "Formal" in rendered
    assert "Register:" in rendered
    assert "Informative" in rendered
    assert "To persuade the reader that media literacy matters." in rendered
    assert "The essay effectively fulfills its persuasive purpose." in rendered
    assert "alignment-note" in rendered
    assert "Contextual Suggestions" in rendered
    assert "Some sentences sound conversational." in rendered
    assert "Use more formal phrasing in the opening." in rendered
    assert "suggestion-recommendation" in rendered
    assert "priority-pill is-high" in rendered
    assert "priority-pill is-medium" in rendered
    assert "priority-pill is-low" in rendered
    assert "High" in rendered
    assert "Medium" in rendered
    assert "Low" in rendered
    assert "ai-text" in rendered


def test_contextual_partial_escapes_suggestion_output():
    rendered = _render(
        "partials/contextual_results.html",
        error=None,
        tone="formal",
        register="informative",
        intent="to inform",
        alignment_score=3,
        alignment_note="",
        model_used="gemini-x",
        truncated=False,
        suggestions=[
            {
                "category": "<b>Clarity</b>",
                "issue": "<script>alert(1)</script>",
                "recommendation": "Use <em>clearer</em> wording.",
                "priority": "high",
            }
        ],
    )

    assert "<script>" not in rendered
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in rendered
    assert "<b>" not in rendered
    assert "<em>" not in rendered


def test_structure_service_returns_friendly_error(monkeypatch):
    async def failing(prompt):
        return None, None, "HTTP 404: model gemini-3.8-flash not found"

    monkeypatch.setattr(structure_service, "generate_json", failing)
    result = asyncio.run(structure_service.analyze_structure("word " * 40))

    assert result["error"] == structure_service.UNAVAILABLE
    assert "gemini-3.8-flash" not in result["error"]


def test_structural_prompt_penalizes_semantic_incoherence():
    from app.services.gemini import PROMPT_DIR

    prompt = (PROMPT_DIR / "structural.txt").read_text(encoding="utf-8")

    assert "CRITICAL RULE: Evaluate semantic coherence." in prompt
    assert "recipe into a narrative story" in prompt
    assert "give them a 1 or 2 out of 5" in prompt
    assert 'explicitly quote it in "original_text"' in prompt
    assert 'Use "issue_type": "Off-Topic".' in prompt


def test_contextual_prompt_fails_severe_topic_shift():
    from app.services.gemini import PROMPT_DIR

    prompt = (PROMPT_DIR / "contextual.txt").read_text(encoding="utf-8")

    assert '"alignment_score" MUST be 1/5' in prompt
    assert "fails due to a severe topic shift" in prompt
    assert (
        'include the irrelevant section as a "high" priority suggestion'
        in prompt
    )


def test_contextual_service_injects_essay_type_into_prompt(monkeypatch):
    from app.services import contextual as contextual_service

    captured = {}

    async def fake(prompt):
        captured["prompt"] = prompt
        return {
            "tone": "reflective",
            "register": "semi-formal",
            "intent": "to entertain",
            "alignment_score": 4,
            "alignment_note": "",
            "suggestions": [],
        }, "gemini-x", None

    monkeypatch.setattr(contextual_service, "generate_json", fake)
    result = asyncio.run(
        contextual_service.analyze_contextual("word " * 40, "creative")
    )

    assert result["error"] is None
    assert result["alignment_score"] == 4
    assert "ESSAY TYPE: creative" in captured["prompt"]


def test_structure_service_rejects_short_essay():
    result = asyncio.run(structure_service.analyze_structure("too short"))
    assert "too short" in result["error"]


def test_structure_service_marks_truncated(monkeypatch):
    async def fake(prompt):
        return {"overall": {"score": 4, "summary": "ok"}}, "gemini-x", None

    monkeypatch.setattr(structure_service, "generate_json", fake)
    result = asyncio.run(structure_service.analyze_structure("word " * 5000))
    assert result["truncated"] is True


def test_structure_service_returns_specific_suggestions(monkeypatch):
    async def fake(prompt):
        return {
            "overall": {"score": 4, "summary": "ok"},
            "specific_suggestions": [
                {
                    "issue_type": "Word Choice",
                    "original_text": "very good",
                    "suggestion": "compelling",
                    "reason": "More precise and academic.",
                },
                "not a suggestion object",
            ],
        }, "groq/openai/gpt-oss-20b", None

    monkeypatch.setattr(structure_service, "generate_json", fake)
    result = asyncio.run(structure_service.analyze_structure("word " * 40))

    assert result["specific_suggestions"] == [
        {
            "issue_type": "Word Choice",
            "original_text": "very good",
            "suggestion": "compelling",
            "reason": "More precise and academic.",
            "score": 0,
        }
    ]


def test_structure_service_defaults_specific_suggestions_to_empty(monkeypatch):
    async def fake(prompt):
        return {"overall": {"score": 4, "summary": "ok"}}, "groq/openai/gpt-oss-20b", None

    monkeypatch.setattr(structure_service, "generate_json", fake)
    result = asyncio.run(structure_service.analyze_structure("word " * 40))

    assert result["specific_suggestions"] == []


def test_analysis_route_normalizes_essay_type(
    client, monkeypatch, scheduled_profile_updates
):
    captured = {}

    async def fake(text, essay_type, user_profile=None):
        captured["essay_type"] = essay_type
        captured["user_profile"] = user_profile
        return {
            "scores": {"overall": 4},
            "feedback": {},
            "summary": "",
            "model_used": "gemini-x",
            "truncated": False,
            "error": None,
        }

    monkeypatch.setattr("app.routers.analysis.analyze_structure", fake)
    response = client.post(
        "/api/analyze/structure",
        data={"plain_text": "word " * 40, "essay_type": "haiku"},
    )

    assert response.status_code == 200
    assert captured["essay_type"] == "expository"


def test_structure_route_schedules_profile_update_with_token(
    client, monkeypatch, scheduled_profile_updates
):
    """The task must get the access token, never the request-scoped client."""

    async def fake(text, essay_type, user_profile=None):
        return {
            "scores": {"overall": 4},
            "feedback": {},
            "summary": "",
            "model_used": "gemini-x",
            "truncated": False,
            "error": None,
        }

    monkeypatch.setattr("app.routers.analysis.analyze_structure", fake)
    text = "word " * 40
    response = client.post(
        "/api/analyze/structure",
        data={"plain_text": text, "essay_type": "expository"},
    )

    assert response.status_code == 200
    assert len(scheduled_profile_updates) == 1
    call = scheduled_profile_updates[0]
    assert call["user_id"] == USER_ID
    assert call["access_token"] == "test-token"
    assert call["text"] == text
    assert call["essay_type"] == "expository"


def test_contextual_route_schedules_profile_update_with_token(
    client, monkeypatch, scheduled_profile_updates
):
    async def fake(text, essay_type, user_profile=None):
        return {
            "tone": "reflective",
            "register": "formal",
            "intent": "",
            "alignment_score": 4,
            "alignment_note": "",
            "suggestions": [],
            "model_used": "gemini-x",
            "truncated": False,
            "error": None,
        }

    monkeypatch.setattr("app.routers.analysis.analyze_contextual", fake)
    response = client.post(
        "/api/analyze/contextual",
        data={"plain_text": "word " * 40, "essay_type": "expository"},
    )

    assert response.status_code == 200
    assert len(scheduled_profile_updates) == 1
    assert scheduled_profile_updates[0]["access_token"] == "test-token"
    assert scheduled_profile_updates[0]["essay_type"] == "expository"


def test_short_text_does_not_schedule_profile_update(
    client, monkeypatch, scheduled_profile_updates
):
    async def fake(text, essay_type, user_profile=None):
        return {
            "scores": {"overall": 4},
            "feedback": {},
            "summary": "",
            "model_used": "gemini-x",
            "truncated": False,
            "error": None,
        }

    monkeypatch.setattr("app.routers.analysis.analyze_structure", fake)
    client.post(
        "/api/analyze/structure",
        data={"plain_text": "word " * 10, "essay_type": "expository"},
    )

    assert scheduled_profile_updates == []


def test_saved_profile_is_injected_and_throttled(
    client, fake_db, monkeypatch, scheduled_profile_updates
):
    """A recent profile feeds the prompt; the cheap Welford update still runs
    (LLM throttling now happens inside the style profiler)."""
    fake_db.profiles["p1"] = {
        "id": "p1",
        "user_id": USER_ID,
        "essay_type": "expository",
        "tone": "reflective",
        "register": "informal",
        "updated_at": datetime.now(UTC).isoformat(),
    }
    captured = {}

    async def fake(text, essay_type, user_profile=None):
        captured["user_profile"] = user_profile
        return {
            "scores": {"overall": 4},
            "feedback": {},
            "summary": "",
            "model_used": "gemini-x",
            "truncated": False,
            "error": None,
        }

    monkeypatch.setattr("app.routers.analysis.analyze_structure", fake)
    response = client.post(
        "/api/analyze/structure",
        data={"plain_text": "word " * 40, "essay_type": "expository"},
    )

    assert response.status_code == 200
    assert captured["user_profile"]["tone"] == "reflective"
    assert len(scheduled_profile_updates) == 1


def test_profile_throttle_is_scoped_to_persona(
    client, fake_db, monkeypatch, scheduled_profile_updates
):
    """A fresh creative profile must not suppress an expository refresh."""
    fake_db.profiles["creative"] = {
        "id": "creative",
        "user_id": USER_ID,
        "essay_type": "creative",
        "tone": "playful",
        "register": "informal",
        "updated_at": datetime.now(UTC).isoformat(),
    }
    fake_db.profiles["expository"] = {
        "id": "expository",
        "user_id": USER_ID,
        "essay_type": "expository",
        "tone": "formal",
        "register": "academic",
        "updated_at": (
            datetime.now(UTC) - timedelta(minutes=10)
        ).isoformat(),
    }
    captured = {}

    async def fake(text, essay_type, user_profile=None):
        captured["user_profile"] = user_profile
        return {
            "scores": {"overall": 4},
            "feedback": {},
            "summary": "",
            "model_used": "gemini-x",
            "truncated": False,
            "error": None,
        }

    monkeypatch.setattr("app.routers.analysis.analyze_structure", fake)
    client.post(
        "/api/analyze/structure",
        data={"plain_text": "word " * 40, "essay_type": "expository"},
    )

    assert captured["user_profile"]["tone"] == "formal"
    assert len(scheduled_profile_updates) == 1
    assert scheduled_profile_updates[0]["essay_type"] == "expository"


def test_stale_profile_is_refreshed(
    client, fake_db, monkeypatch, scheduled_profile_updates
):
    stale = datetime.now(UTC) - timedelta(minutes=10)
    fake_db.profiles["p1"] = {
        "id": "p1",
        "user_id": USER_ID,
        "essay_type": "expository",
        "tone": "reflective",
        "register": "informal",
        "updated_at": stale.isoformat(),
    }

    async def fake(text, essay_type, user_profile=None):
        return {
            "scores": {"overall": 4},
            "feedback": {},
            "summary": "",
            "model_used": "gemini-x",
            "truncated": False,
            "error": None,
        }

    monkeypatch.setattr("app.routers.analysis.analyze_structure", fake)
    client.post(
        "/api/analyze/structure",
        data={"plain_text": "word " * 40, "essay_type": "expository"},
    )

    assert len(scheduled_profile_updates) == 1


def test_analysis_rejects_oversized_input(client, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "MAX_PLAIN_TEXT_CHARS", 10)
    response = client.post(
        "/api/analyze/structure",
        data={"plain_text": "x" * 50, "essay_type": "expository"},
    )

    assert response.status_code == 200
    assert "too long" in response.text.lower()


def test_update_user_style_profile_upserts_with_fresh_client(monkeypatch, fake_db):
    """The task builds its own client and writes running stylometric stats."""
    from app.services import style_profiler
    from app.services.stylometry import FEATURE_COUNT

    async def fake_metrics(text):
        return {
            "tone": "bold",
            "register": "informal",
            "avg_sentence_length": 12,
            "vocabulary_richness": 0.6,
        }

    monkeypatch.setattr(style_profiler, "_extract_metrics", fake_metrics)
    created = {}

    def fake_client(token):
        created["token"] = token
        return fake_db

    monkeypatch.setattr(style_profiler, "get_supabase_with_token", fake_client)

    result = asyncio.run(
        style_profiler.update_user_style_profile(
            USER_ID, "word " * 40, "test-token", "creative"
        )
    )

    assert result["success"] is True
    assert created["token"] == "test-token"
    assert len(fake_db.profiles) == 1
    row = next(iter(fake_db.profiles.values()))
    assert row["user_id"] == USER_ID
    assert row["essay_type"] == "creative"
    assert row["tone"] == "bold"
    assert row["essay_count"] == 1
    assert len(row["feature_mean"]) == FEATURE_COUNT
    assert len(row["feature_m2"]) == FEATURE_COUNT


def test_update_user_style_profile_keeps_one_row_per_persona(
    monkeypatch, fake_db
):
    from app.services import style_profiler

    async def fake_metrics(text):
        return {"tone": "bold", "register": "formal"}

    monkeypatch.setattr(style_profiler, "_extract_metrics", fake_metrics)
    monkeypatch.setattr(
        style_profiler, "get_supabase_with_token", lambda token: fake_db
    )

    for essay_type in ("creative", "creative", "expository"):
        result = asyncio.run(
            style_profiler.update_user_style_profile(
                USER_ID, "word " * 40, "test-token", essay_type
            )
        )
        assert result["success"] is True

    assert len(fake_db.profiles) == 2
    assert {
        row["essay_type"] for row in fake_db.profiles.values()
    } == {"creative", "expository"}


def test_update_user_style_profile_uses_fallback_tone_when_metrics_fail(
    monkeypatch, fake_db
):
    """A failed AI call must not overwrite a saved tone/register."""
    from app.services import style_profiler

    async def failing_metrics(text):
        raise style_profiler.StyleProfileError("All AI models failed: HTTP 429")

    monkeypatch.setattr(style_profiler, "_extract_metrics", failing_metrics)
    monkeypatch.setattr(
        style_profiler, "get_supabase_with_token", lambda token: fake_db
    )
    fake_db.profiles["p1"] = {
        "id": "p1",
        "user_id": USER_ID,
        "essay_type": "expository",
        "tone": "formal",
        "register": "academic",
    }

    result = asyncio.run(
        style_profiler.update_user_style_profile(
            USER_ID, "word " * 40, "test-token", "expository"
        )
    )

    assert result["success"] is True
    row = next(iter(fake_db.profiles.values()))
    assert row["tone"] == "formal"
    assert row["register"] == "academic"


def test_structure_partial_renders_drift_warning_on_success():
    rendered = _render(
        "partials/structure_results.html",
        error=None,
        drift_warning="This looks different from your usual 'formal' tone.",
        scores={"overall": 4},
        feedback={},
        summary="",
        model_used="groq/openai/gpt-oss-20b",
        truncated=False,
    )
    assert "Style Drift Detected" in rendered
    assert "usual 'formal' tone" in html.unescape(rendered)


def test_contextual_partial_renders_drift_warning_on_success():
    rendered = _render(
        "partials/contextual_results.html",
        error=None,
        drift_warning="This looks different from your usual 'formal' tone.",
        tone="casual",
        register="informal",
        intent="to share",
        alignment_score=3,
        alignment_note="",
        suggestions=[],
        model_used="groq/openai/gpt-oss-20b",
        truncated=False,
    )
    assert "Style Drift Detected" in rendered
    assert "usual 'formal' tone" in html.unescape(rendered)


def test_structure_partial_hides_drift_warning_when_absent():
    rendered = _render(
        "partials/structure_results.html",
        error=None,
        scores={"overall": 4},
        feedback={},
        summary="",
        model_used="groq/openai/gpt-oss-20b",
        truncated=False,
    )
    assert "Style Drift Detected" not in rendered


def test_structure_partial_renders_premium_suggestion_layout():
    rendered = _render(
        "partials/structure_results.html",
        error=None,
        scores={
            "overall": 4,
            "introduction": 5,
            "body": 4,
            "conclusion": 3,
            "flow": 2,
            "tone": None,
        },
        feedback={"introduction": "Strong opening."},
        summary="A clear essay with room to tighten the flow.",
        model_used="groq/openai/gpt-oss-20b",
        truncated=False,
        specific_suggestions=[
            {
                "issue_type": "Word Choice",
                "original_text": "<script>alert(1)</script>",
                "suggestion": "Use <b>better</b> words",
                "reason": "More precise language.",
            }
        ],
    )

    assert "80" in rendered
    assert "data-feedback-score" in rendered
    assert "score-bar-80" in rendered
    assert "Overall writing score" in rendered
    assert "Suggestions" in rendered
    assert "Word Choice" in rendered
    assert "suggestion-quote" in rendered
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in rendered
    assert "<script>" not in rendered
    assert "suggestion-recommendation" in rendered
    assert "&lt;b&gt;better&lt;/b&gt;" in rendered
    assert "suggestion-desc" in rendered
    assert "A clear essay with room to tighten the flow." in rendered
    assert "summary-box" in rendered
    assert "score-pill is-strong" in rendered
    assert "score-pill is-mid" in rendered
    assert "score-pill is-weak" in rendered
    assert "score-pill" in rendered


def test_structure_partial_shows_empty_suggestions_state():
    rendered = _render(
        "partials/structure_results.html",
        error=None,
        scores={"overall": None},
        feedback={},
        summary="",
        model_used="groq/openai/gpt-oss-20b",
        truncated=False,
        specific_suggestions=[],
    )

    assert "No specific suggestions were returned" in rendered
    assert "—" in rendered
    assert "data-feedback-score" in rendered
    assert "score-bar-0" in rendered


def test_structure_route_passes_drift_warning_to_template(
    client, monkeypatch
):
    async def fake_drift(
        user_id, text, essay_type, supabase, route_name="analysis"
    ):
        return "This looks very different from your usual 'formal' tone."

    async def fake_structure(text, essay_type, user_profile=None):
        return {
            "scores": {"overall": 4},
            "feedback": {},
            "summary": "",
            "model_used": "groq/openai/gpt-oss-20b",
            "truncated": False,
            "error": None,
        }

    monkeypatch.setattr("app.routers.analysis._drift_warning", fake_drift)
    monkeypatch.setattr("app.routers.analysis.analyze_structure", fake_structure)

    response = client.post(
        "/api/analyze/structure",
        data={"plain_text": "word " * 40, "essay_type": "expository"},
    )

    assert response.status_code == 200
    assert "Style Drift Detected" in response.text
    assert "usual 'formal' tone" in html.unescape(response.text)


def test_structure_route_detects_formal_to_casual_drift(
    client, fake_db, monkeypatch
):
    """End-to-end: a formal saved profile + casual paste renders the banner."""
    from app.services import style_profiler

    formal = (
        "The proliferation of social media has fundamentally altered the "
        "nature of public discourse. Whereas previous generations relied upon "
        "editorial gatekeepers to curate information, contemporary citizens "
        "encounter an unrestricted stream of content designed to maximize "
        "engagement rather than understanding. This transformation carries "
        "profound implications for democratic deliberation."
    )
    casual = (
        "yo whats up fam, this is a casual tiktok message lol. lowkey just "
        "vibes today, no cap. anyway wyd later? we should link up and maybe "
        "grab food or something idk. my day was so mid honestly."
    )

    from app.services.stylometry import FEATURE_COUNT, style_features

    baseline = style_features(formal)
    fake_db.profiles["p1"] = {
        "id": "p1",
        "user_id": USER_ID,
        "essay_type": "expository",
        "tone": "formal",
        "register": "academic",
        "feature_mean": baseline,
        "feature_m2": [0.02] * FEATURE_COUNT,
        "essay_count": 10,
    }

    async def fake_structure(text, essay_type, user_profile=None):
        return {
            "scores": {"overall": 4},
            "feedback": {},
            "summary": "",
            "model_used": "groq/openai/gpt-oss-20b",
            "truncated": False,
            "error": None,
        }

    monkeypatch.setattr(
        "app.routers.analysis.analyze_structure", fake_structure)

    response = client.post(
        "/api/analyze/structure",
        data={"plain_text": casual, "essay_type": "expository"},
    )

    assert response.status_code == 200
    assert "Style Drift Detected" in response.text
    assert "usual 'formal' tone" in html.unescape(response.text)


def test_structure_route_uses_persona_for_drift_and_profile_update(
    client, monkeypatch, scheduled_profile_updates
):
    captured = {}

    async def fake_drift(
        user_id, text, essay_type, supabase, route_name="analysis"
    ):
        captured["persona"] = essay_type
        return None

    async def fake_structure(text, essay_type, user_profile=None):
        return {
            "scores": {"overall": 4},
            "feedback": {},
            "summary": "",
            "model_used": "groq/openai/gpt-oss-20b",
            "truncated": False,
            "error": None,
        }

    monkeypatch.setattr("app.routers.analysis._drift_warning", fake_drift)
    monkeypatch.setattr("app.routers.analysis.analyze_structure", fake_structure)

    response = client.post(
        "/api/analyze/structure",
        data={"plain_text": "word " * 40, "essay_type": "creative"},
    )

    assert response.status_code == 200
    assert captured["persona"] == "creative"
    assert scheduled_profile_updates[0]["essay_type"] == "creative"


def test_check_style_drift_skips_missing_persona(monkeypatch, fake_db):
    from app.services import style_profiler

    fake_db.profiles["p1"] = {
        "id": "p1",
        "user_id": USER_ID,
        "essay_type": "creative",
        "tone": "informal",
        "feature_mean": [0.0] * 10,
    }

    result = asyncio.run(
        style_profiler.check_style_drift(
            USER_ID, "word " * 40, "expository", fake_db
        )
    )

    assert result["error"] == "No saved profile for this persona"


def test_check_style_drift_returns_not_enough_history(fake_db):
    from app.services import style_profiler
    from app.services.stylometry import FEATURE_COUNT

    fake_db.profiles["p1"] = {
        "id": "p1",
        "user_id": USER_ID,
        "essay_type": "expository",
        "tone": "formal",
        "feature_mean": [0.0] * FEATURE_COUNT,
        "feature_m2": [0.0] * FEATURE_COUNT,
        "essay_count": 2,
    }

    result = asyncio.run(
        style_profiler.check_style_drift(
            USER_ID, "word " * 40, "expository", fake_db
        )
    )

    assert result["is_drastic"] is False
    assert result["reason"] == "not enough history"
    assert result["changes"] == []


def test_check_style_drift_selects_the_requested_persona(fake_db):
    from app.services import style_profiler
    from app.services.stylometry import FEATURE_COUNT, style_features

    text = "word " * 40
    features = style_features(text)
    fake_db.profiles["p1"] = {
        "id": "p1",
        "user_id": USER_ID,
        "essay_type": "argumentative",
        "tone": "formal",
        "feature_mean": features,
        "feature_m2": [0.02] * FEATURE_COUNT,
        "essay_count": 10,
    }
    fake_db.profiles["p2"] = {
        "id": "p2",
        "user_id": USER_ID,
        "essay_type": "creative",
        "tone": "casual",
        "feature_mean": features,
        "feature_m2": [0.02] * FEATURE_COUNT,
        "essay_count": 10,
    }

    match = asyncio.run(
        style_profiler.check_style_drift(USER_ID, text, "argumentative", fake_db)
    )
    other = asyncio.run(
        style_profiler.check_style_drift(USER_ID, text, "creative", fake_db)
    )

    assert match["saved_tone"] == "formal"
    assert match["is_drastic"] is False
    assert other["saved_tone"] == "casual"


def test_groq_retries_without_json_mode_on_validation_error(monkeypatch):
    from app.services import gemini

    class FakeResponse:
        def __init__(self, status_code, text, payload=None):
            self.status_code = status_code
            self.text = text
            self._payload = payload or {}

        def json(self):
            return self._payload

    responses = [
        FakeResponse(
            400,
            json.dumps({"error": {"message": "Failed to validate JSON."}}),
        ),
        FakeResponse(
            200,
            '{"choices":[{"message":{"content":"{\\"tone\\":\\"bold\\"}"}}]}',
            {
                "choices": [
                    {"message": {"content": '{"tone":"bold"}'}}
                ]
            },
        ),
    ]
    payloads = []

    async def fake_post(url, headers, payload):
        payloads.append(dict(payload))
        return responses.pop(0)

    monkeypatch.setattr(gemini, "_post", fake_post)

    text, error = asyncio.run(
        gemini._call_provider(
            "groq", "openai/gpt-oss-20b", "Return JSON.", "test-key"
        )
    )

    assert error is None
    assert text == '{"tone":"bold"}'
    assert len(payloads) == 2
    assert "response_format" in payloads[0]
    assert "response_format" not in payloads[1]
