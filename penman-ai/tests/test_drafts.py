"""Draft save/delete behaviour."""
import json

HTMX = {"HX-Request": "true"}


def _payload(**overrides):
    data = {
        "draft_id": "",
        "title": "My Essay",
        "essay_type": "expository",
        "content": "<p>Hello world</p>",
        "plain_text": "Hello world",
        "word_count": "2",
    }
    data.update(overrides)
    return data


def test_save_then_save_again_updates_same_row(client, store):
    first = client.post("/drafts/save", data=_payload(), headers=HTMX)
    assert first.status_code == 200

    draft_id = json.loads(first.headers["HX-Trigger"])["draftSaved"]["id"]
    assert len(store) == 1

    second = client.post(
        "/drafts/save",
        data=_payload(draft_id=draft_id, title="Renamed"),
        headers=HTMX,
    )
    assert second.status_code == 200

    saved = json.loads(second.headers["HX-Trigger"])["draftSaved"]
    assert saved["id"] == draft_id
    assert saved["action"] == "Updated"
    assert len(store) == 1
    assert store[draft_id]["title"] == "Renamed"


def test_save_unknown_draft_returns_404(client):
    response = client.post(
        "/drafts/save",
        data=_payload(draft_id="22222222-2222-2222-2222-222222222222"),
        headers=HTMX,
    )
    assert response.status_code == 404
    assert "not found" in response.text.lower()


def test_save_rejects_malformed_draft_id(client, store):
    response = client.post(
        "/drafts/save", data=_payload(draft_id="not-a-uuid"), headers=HTMX
    )
    assert response.status_code == 400
    assert store == {}


def test_save_normalizes_unknown_essay_type(client, store):
    client.post("/drafts/save", data=_payload(essay_type="haiku"), headers=HTMX)
    assert next(iter(store.values()))["essay_type"] == "expository"


def test_save_caps_title_length(client, store):
    client.post("/drafts/save", data=_payload(title="x" * 500), headers=HTMX)
    assert len(next(iter(store.values()))["title"]) == 200


def test_save_rejects_oversized_content(client, store, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "MAX_CONTENT_CHARS", 10)
    response = client.post("/drafts/save", data=_payload(content="x" * 50), headers=HTMX)
    assert response.status_code == 413
    assert store == {}


def test_save_reports_errors_without_leaking_exception_text(client):
    class Exploding:
        def table(self, name):
            raise RuntimeError("connection string postgres://secret")

    from app.dependencies import get_user_supabase
    from app.main import app

    app.dependency_overrides[get_user_supabase] = lambda: Exploding()
    response = client.post("/drafts/save", data=_payload(), headers=HTMX)

    assert response.status_code == 500
    assert "postgres://secret" not in response.text
    assert "Could not save your draft" in response.text


def test_delete_removes_own_draft(client, store):
    client.post("/drafts/save", data=_payload(), headers=HTMX)
    draft_id = next(iter(store))

    response = client.post(f"/drafts/{draft_id}/delete")
    assert response.status_code == 303
    assert response.headers["location"] == "/drafts?deleted=1"
    assert store == {}


def test_delete_does_not_touch_other_users_draft(client, store):
    draft_id = "33333333-3333-3333-3333-333333333333"
    store[draft_id] = {"id": draft_id, "user_id": "someone-else", "title": "Theirs"}

    response = client.post(f"/drafts/{draft_id}/delete")
    assert response.status_code == 303
    assert "error" in response.headers["location"]
    assert draft_id in store
