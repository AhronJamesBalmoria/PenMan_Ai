"""Page rendering and consent gating."""
from types import SimpleNamespace

from conftest import USER_ID


def test_editor_renders_for_consented_user(client, fake_db):
    fake_db.profiles[USER_ID] = {"id": USER_ID, "consent_given": True}

    response = client.get("/editor")

    assert response.status_code == 200
    assert 'id="draft-form"' in response.text
    assert 'id="draft-id"' in response.text
    # The divs in the editor layout must balance out.
    assert response.text.count('<div') == response.text.count('</div>')


def test_editor_redirects_to_consent_without_consent(client, fake_db):
    fake_db.profiles[USER_ID] = {"id": USER_ID, "consent_given": False}

    response = client.get("/editor")

    assert response.status_code == 303
    assert response.headers["location"] == "/consent"


def test_editor_renders_stored_draft(client, fake_db, store):
    fake_db.profiles[USER_ID] = {"id": USER_ID, "consent_given": True}
    draft_id = "44444444-4444-4444-4444-444444444444"
    store[draft_id] = {
        "id": draft_id,
        "user_id": USER_ID,
        "title": "Stored Essay",
        "essay_type": "persuasive",
        "content": "<p>Body</p>",
        "plain_text": "Body",
        "word_count": 1,
        "updated_at": "2026-01-01T10:00:00+00:00",
    }

    response = client.get(f"/editor?draft_id={draft_id}")

    assert response.status_code == 200
    assert "Stored Essay" in response.text
    assert 'value="persuasive" selected' in response.text


def test_drafts_page_shows_delete_flash(client, fake_db):
    fake_db.profiles[USER_ID] = {"id": USER_ID, "consent_given": True}

    response = client.get("/drafts?deleted=1")

    assert response.status_code == 200
    assert "Draft deleted." in response.text


def test_drafts_page_shows_error_flash(client, fake_db):
    fake_db.profiles[USER_ID] = {"id": USER_ID, "consent_given": True}

    response = client.get("/drafts?error=Could+not+delete+that+draft.")

    assert response.status_code == 200
    assert "Could not delete that draft." in response.text


def test_consent_creates_missing_profile_row(client, fake_db):
    assert fake_db.profiles == {}

    response = client.post(
        "/auth/consent",
        data={"privacy_consent": "on", "academic_consent": "on"},
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/editor"
    assert fake_db.profiles[USER_ID]["consent_given"] is True


def test_consent_without_writable_profile_reports_error(client, fake_db, monkeypatch):
    """A failed write must show an error instead of looping /consent → /editor."""

    class EmptyResult:
        def update(self, payload):
            return self

        def upsert(self, payload):
            return self

        def eq(self, *args):
            return self

        def execute(self):
            return SimpleNamespace(data=[])

    monkeypatch.setattr(fake_db, "table", lambda name: EmptyResult())

    response = client.post(
        "/auth/consent",
        data={"privacy_consent": "on", "academic_consent": "on"},
    )

    assert response.status_code == 500
    assert "could not save your consent" in response.text.lower()


def test_health_endpoint(anonymous_client):
    response = anonymous_client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
