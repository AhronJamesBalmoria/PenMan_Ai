"""Render checks for the refreshed intro, login, register and reset pages."""

NAV_LOGIN = '<a href="/login" class="nav-link-custom">Login</a>'
NAV_REGISTER = '<a href="/register" class="btn btn-sm btn-primary rounded-pill px-3">Register</a>'


def _no_nav_auth_links(body):
    assert NAV_LOGIN not in body
    assert NAV_REGISTER not in body


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
    # Public pages keep the navbar actions.
    assert NAV_LOGIN in r.text
    assert NAV_REGISTER in r.text


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
    # Login must not enforce a password length: short/legacy passwords are
    # valid for Supabase, and the rule belongs to registration/reset only.
    assert "data-password-minlength" not in r.text
    _no_nav_auth_links(r.text)


def test_register_page_new_design(anonymous_client):
    r = anonymous_client.get("/register")
    assert r.status_code == 200
    for text in ("Create your account", "Username", "At least 8 characters",
                 "Terms of Service", "Privacy Notice", "Create account"):
        assert text in r.text
    assert "I agree to the" in r.text
    assert "Privacy Notice" in r.text
    assert "I have read the" not in r.text
    assert "At least 8 characters" in r.text
    assert "Full name" not in r.text
    assert 'minlength="8"' in r.text
    assert 'data-password-minlength="8"' in r.text
    assert 'href="/terms"' in r.text
    assert 'href="/privacy"' in r.text
    # Legal links open in a new tab so the filled form is not lost.
    assert 'href="/terms" target="_blank" rel="noopener noreferrer"' in r.text
    assert 'href="/privacy" target="_blank" rel="noopener noreferrer"' in r.text
    assert "opens in a new tab" in r.text
    assert "auth-tabs" not in r.text
    assert "novalidate" in r.text
    assert "bi-arrow-right" not in r.text
    _no_nav_auth_links(r.text)


def test_forgot_password_page(anonymous_client):
    r = anonymous_client.get("/forgot-password")
    assert r.status_code == 200
    assert "Reset your password" in r.text
    assert 'action="/auth/forgot-password"' in r.text
    assert "←" not in r.text
    _no_nav_auth_links(r.text)


def test_reset_password_page(anonymous_client):
    r = anonymous_client.get("/reset-password")
    assert r.status_code == 200
    assert "Choose a new password" in r.text
    assert 'name="access_token"' in r.text
    assert 'name="refresh_token"' in r.text
    assert 'name="token_hash"' in r.text
    assert 'data-password-minlength="8"' in r.text
    assert 'id="reset-config"' in r.text
    assert "https://test.supabase.co" in r.text
    assert "@supabase/supabase-js" in r.text
    assert "At least 8 characters" in r.text
    _no_nav_auth_links(r.text)


def test_public_terms_page(anonymous_client):
    r = anonymous_client.get("/terms")
    assert r.status_code == 200
    assert "Terms of Service" in r.text
    assert "Academic integrity" in r.text
    assert "not a plagiarism or AI-detection tool" in r.text
    assert "AI can be wrong" in r.text
    assert "No warranty and limits on liability" in r.text
    # Removed sections must not linger.
    assert "governing law" not in r.text
    assert "←" not in r.text
    assert "legal-card" in r.text


def test_public_privacy_page(anonymous_client):
    r = anonymous_client.get("/privacy")
    assert r.status_code == 200
    assert "Privacy Policy" in r.text
    assert "Groq" in r.text
    assert "Data Privacy Act of 2012" in r.text
    assert "Reset baseline" in r.text
    assert "Research use" not in r.text
    assert "Who we are" not in r.text
    assert "National Privacy Commission" not in r.text
    assert "←" not in r.text
    assert "legal-card" in r.text


def test_landing_has_single_footer(anonymous_client):
    r = anonymous_client.get("/")
    assert r.status_code == 200
    assert "Academic Integrity Notice" in r.text


def test_auth_flash_params_are_cleared_from_url():
    from app.config import BASE_DIR

    js = (BASE_DIR / "static" / "js" / "auth.js").read_text(encoding="utf-8")
    assert "history.replaceState" in js
    assert "'error', 'info', 'reset'" in js


def test_password_toggle_stays_aligned_when_error_appears():
    from app.config import BASE_DIR

    css = (BASE_DIR / "static" / "css" / "style.css").read_text(encoding="utf-8")
    rule = css.split(".password-toggle {", 1)[1].split("}", 1)[0]
    assert "top: 0.55rem" in rule
    assert "translateY(-50%)" not in rule



def test_register_page_shows_query_error(anonymous_client):
    r = anonymous_client.get(
        '/register',
        params={'error': 'An account with this email already exists. Try logging in.'},
    )
    assert r.status_code == 200
    assert 'already exists' in r.text


def test_login_page_shows_query_error(anonymous_client):
    r = anonymous_client.get('/login', params={'error': 'Invalid email or password.'})
    assert r.status_code == 200
    assert 'Invalid email or password' in r.text



def test_register_with_session_goes_straight_to_drafts(anonymous_client, monkeypatch):
    """With Confirm email off, Supabase returns a session: no email needed."""
    from types import SimpleNamespace

    from conftest import USER_ID

    import app.routers.auth as auth_module

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
              "display_name": "New User", "terms_agreed": "on"},
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/drafts"


def test_register_requires_terms_acceptance(anonymous_client, monkeypatch):
    """A client-side checkbox is not consent: the server must refuse it."""
    import app.routers.auth as auth_module

    def must_not_be_called():
        raise AssertionError("get_supabase must not run without terms consent")

    monkeypatch.setattr(auth_module, "get_supabase", must_not_be_called)

    r = anonymous_client.post(
        "/auth/register",
        data={"email": "new@example.com", "password": "password123"},
    )
    assert r.status_code == 303
    assert "error=" in r.headers["location"]
    assert "accept" in r.headers["location"].lower()
