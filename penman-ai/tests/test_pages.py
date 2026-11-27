"""Page rendering after the consent-flow removal."""
from conftest import USER_ID


def test_editor_renders(client):
    response = client.get("/editor")

    assert response.status_code == 200
    assert 'id="draft-form"' in response.text
    assert 'id="draft-id"' in response.text
    assert "feedback-sticky" in response.text
    assert "nav nav-pills" in response.text
    assert "placeholder-glow" in response.text
    assert "data-feedback-tab" in response.text
    assert '<span id="word-count-top">0</span> words' in response.text
    # The divs in the editor layout must balance out.
    assert response.text.count('<div') == response.text.count('</div>')


def test_editor_renders_stored_draft(client, store):
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


def test_drafts_page_shows_delete_flash(client):
    response = client.get("/drafts?deleted=1")

    assert response.status_code == 200
    assert "Draft deleted." in response.text


def test_drafts_page_shows_error_flash(client):
    response = client.get("/drafts?error=Could+not+delete+that+draft.")

    assert response.status_code == 200
    assert "Could not delete that draft." in response.text


def test_consent_pages_are_removed(anonymous_client):
    assert anonymous_client.get("/consent").status_code == 404
    assert anonymous_client.post("/auth/consent").status_code == 404


def test_editor_placeholder_aligns_with_caret():
    from app.config import BASE_DIR

    css = (BASE_DIR / "static" / "css" / "style.css").read_text(encoding="utf-8")
    rule = css.split(".ql-editor.ql-blank::before {", 1)[1].split("}", 1)[0]
    assert "left: 1.5rem" in rule
    assert "top: 2.5rem" in rule


def test_reset_menu_is_the_rightmost_footnote_item():
    from app.config import BASE_DIR

    css = (BASE_DIR / "static" / "css" / "style.css").read_text(encoding="utf-8")
    rule = css.split("#baseline-status {", 1)[1].split("}", 1)[0]
    assert "position: absolute" in rule
    assert "right: 0" in rule

    html = (BASE_DIR / "templates" / "editor.html").read_text(encoding="utf-8")
    assert 'id="baseline-status"' in html


def test_health_endpoint(anonymous_client):
    response = anonymous_client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
