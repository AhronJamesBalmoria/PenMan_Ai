"""Render checks for the refreshed intro, login, register and reset pages."""
from conftest import anonymous_client


def test_landing_page(anonymous_client):
    r = anonymous_client.get("/")
    assert r.status_code == 200
    assert "Write better." in r.text
    assert "Keep your voice." in r.text
    assert "Get instant feedback on clarity, structure," in r.text
    assert "and evidence as you write" in r.text
    assert "Start writing free" in r.text
    assert "Feedback for every layer" in r.text
    assert "AI feedback for stronger writing" not in r.text
    assert "No credit card" not in r.text
    assert "Your ideas stay yours" not in r.text
    assert "bi-arrow-right" not in r.text


def test_login_page_new_design(anonymous_client):
    r = anonymous_client.get("/login")
    assert r.status_code == 200
    for text in ("Welcome back", "Forgot password?", "Continue to workspace",
                 "Create an account", 'name="email"', 'name="password"'):
        assert text in r.text
    assert "Keep me signed in" not in r.text
    assert "minlength" not in r.text  # no min length on login
    assert "auth-tabs" not in r.text
    assert "novalidate" in r.text
    assert "bi-arrow-right" not in r.text


def test_register_page_new_design(anonymous_client):
    r = anonymous_client.get("/register")
    assert r.status_code == 200
    for text in ("Create your account", "Username", "At least 8 characters",
                 "Terms of Service", "Privacy Policy", "Create account"):
        assert text in r.text
    assert "Full name" not in r.text
    assert 'minlength="8"' in r.text
    assert 'href="/terms"' in r.text
    assert 'href="/privacy"' in r.text
    assert "auth-tabs" not in r.text
    assert "novalidate" in r.text
    assert "bi-arrow-right" not in r.text


def test_forgot_password_page(anonymous_client):
    r = anonymous_client.get("/forgot-password")
    assert r.status_code == 200
    assert "Reset your password" in r.text
    assert 'action="/auth/forgot-password"' in r.text


def test_reset_password_page(anonymous_client):
    r = anonymous_client.get("/reset-password")
    assert r.status_code == 200
    assert "Choose a new password" in r.text
    assert 'name="access_token"' in r.text
    assert 'name="refresh_token"' in r.text


def test_public_terms_page(anonymous_client):
    r = anonymous_client.get("/terms")
    assert r.status_code == 200
    assert "Terms of Service" in r.text
    assert "Academic integrity" in r.text


def test_public_privacy_page(anonymous_client):
    r = anonymous_client.get("/privacy")
    assert r.status_code == 200
    assert "Privacy Policy" in r.text
    assert "Groq" in r.text


def test_landing_has_single_footer(anonymous_client):
    r = anonymous_client.get("/")
    assert r.status_code == 200
    assert "Academic Integrity Notice" in r.text



def test_register_page_shows_query_error(anonymous_client):
    r = anonymous_client.get('/register', params={'error': 'An account with this email already exists. Try logging in.'})
    assert r.status_code == 200
    assert 'already exists' in r.text


def test_login_page_shows_query_error(anonymous_client):
    r = anonymous_client.get('/login', params={'error': 'Invalid email or password.'})
    assert r.status_code == 200
    assert 'Invalid email or password' in r.text



def test_register_with_session_goes_straight_to_consent(anonymous_client, monkeypatch):
    """With Confirm email off, Supabase returns a session: no email needed."""
    from types import SimpleNamespace
    import app.routers.auth as auth_module
    from conftest import USER_ID

    fresh = "session-token-abc"

    class FakeAuth:
        def sign_up(self, payload):
            return SimpleNamespace(
                user=SimpleNamespace(id=USER_ID),
                session=SimpleNamespace(
                    access_token=fresh, refresh_token="refresh-abc", expires_in=3600
                ),
            )

    class FakeClient:
        auth = FakeAuth()

    monkeypatch.setattr(auth_module, "get_supabase", lambda: FakeClient())

    r = anonymous_client.post(
        "/auth/register",
        data={"email": "new@example.com", "password": "password123",
              "display_name": "New User"},
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/consent"
