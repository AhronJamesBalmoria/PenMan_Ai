"""Analysis routes and partials must degrade gracefully."""
import asyncio
import html

from app.services import structure as structure_service
from app.templating import templates


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
    assert "Alignment: —/5" in rendered


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


def test_structure_service_returns_friendly_error(monkeypatch):
    async def failing(prompt):
        return None, None, "HTTP 404: model gemini-3.8-flash not found"

    monkeypatch.setattr(structure_service, "generate_json", failing)
    result = asyncio.run(structure_service.analyze_structure("word " * 40))

    assert result["error"] == structure_service.UNAVAILABLE
    assert "gemini-3.8-flash" not in result["error"]


def test_structure_service_rejects_short_essay():
    result = asyncio.run(structure_service.analyze_structure("too short"))
    assert "too short" in result["error"]


def test_structure_service_marks_truncated(monkeypatch):
    async def fake(prompt):
        return {"overall": {"score": 4, "summary": "ok"}}, "gemini-x", None

    monkeypatch.setattr(structure_service, "generate_json", fake)
    result = asyncio.run(structure_service.analyze_structure("word " * 5000))
    assert result["truncated"] is True


def test_analysis_route_normalizes_essay_type(client, monkeypatch):
    captured = {}

    async def fake(text, essay_type):
        captured["essay_type"] = essay_type
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


def test_analysis_rejects_oversized_input(client, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "MAX_PLAIN_TEXT_CHARS", 10)
    response = client.post(
        "/api/analyze/structure",
        data={"plain_text": "x" * 50, "essay_type": "expository"},
    )

    assert response.status_code == 200
    assert "too long" in response.text.lower()
