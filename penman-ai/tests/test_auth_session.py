"""Session resolution: local verification, refresh and middleware wiring."""
import asyncio
import html
import time
from types import SimpleNamespace

import jwt
from conftest import USER_ID
from starlette.requests import Request

from app import auth_session
from app.auth_session import Check, SessionTokens, check_local, resolve_session
from app.config import settings


def _token(*, expires_in=3600, secret=None, email="writer@example.com"):
    now = int(time.time())
    payload = {
        "sub": USER_ID,
        "email": email,
        "aud": "authenticated",
        "iss": f"{settings.SUPABASE_URL}/auth/v1",
        "iat": now,
        "exp": now + expires_in,
    }
    return jwt.encode(payload, secret or settings.SUPABASE_JWT_SECRET, algorithm="HS256")


def _request(cookies):
    header = "; ".join(f"{key}={value}" for key, value in cookies.items())
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/editor",
        "headers": [(b"cookie", header.encode())],
    }
    return Request(scope)


def test_check_local_accepts_valid_token():
    state, claims = check_local(_token())
    assert state is Check.FRESH
    assert claims.user_id == USER_ID
    assert claims.email == "writer@example.com"


def test_check_local_flags_expired_token():
    state, _ = check_local(_token(expires_in=-10))
    assert state is Check.EXPIRED


def test_check_local_rejects_bad_signature():
    state, _ = check_local(_token(secret="a-different-signing-secret-0123456789"))
    assert state is Check.INVALID


def test_check_local_flags_token_expiring_inside_leeway():
    state, _ = check_local(_token(expires_in=settings.TOKEN_REFRESH_LEEWAY - 5))
    assert state is Check.EXPIRED


def test_resolve_session_uses_local_claims_without_network(monkeypatch):
    def explode(*args, **kwargs):
        raise AssertionError("should not hit Supabase for a fresh local token")

    monkeypatch.setattr(auth_session, "_remote_user_sync", explode)
    monkeypatch.setattr(auth_session, "_refresh_sync", explode)

    resolved = asyncio.run(
        resolve_session(
            _request({settings.SESSION_COOKIE_NAME: _token()})
        )
    )

    assert resolved.user["id"] == USER_ID
    assert resolved.new_tokens is None


def test_resolve_session_refreshes_expired_token(monkeypatch):
    refreshed = SessionTokens(_token(), "refresh-token-2", 3600)
    monkeypatch.setattr(auth_session, "_refresh_sync", lambda token: refreshed)

    resolved = asyncio.run(
        resolve_session(
            _request(
                {
                    settings.SESSION_COOKIE_NAME: _token(expires_in=-10),
                    settings.REFRESH_COOKIE_NAME: "refresh-token-1",
                }
            )
        )
    )

    assert resolved.user["id"] == USER_ID
    assert resolved.new_tokens is refreshed
    assert resolved.offline is False


def test_resolve_session_without_refresh_clears_cookies():
    resolved = asyncio.run(
        resolve_session(
            _request({settings.SESSION_COOKIE_NAME: _token(expires_in=-10)})
        )
    )

    assert resolved.user is None
    assert resolved.clear_cookies is True


def test_resolve_session_rejects_forged_token_without_network():
    forged = _token(secret="forged-secret-key-0123456789abcdef")
    resolved = asyncio.run(
        resolve_session(_request({settings.SESSION_COOKIE_NAME: forged}))
    )

    assert resolved.user is None
    assert resolved.clear_cookies is True


def test_jwt_shaped_secret_disables_local_check(monkeypatch):
    """Pasting an anon/service key into SUPABASE_JWT_SECRET must not log users out."""
    token = _token()
    monkeypatch.setattr(
        settings,
        "SUPABASE_JWT_SECRET",
        "eyJhbGciOiJIUzI1NiJ9.eyJyb2xlIjoic2VydmljZV9yb2xlIn0.c2lnbmF0dXJl",
    )

    state, claims = check_local(token)
    assert state is Check.UNVERIFIED
    assert claims is None

    seen = []

    def fake_remote(access_token):
        seen.append(access_token)
        return {"id": USER_ID, "email": "writer@example.com", "access_token": access_token}

    monkeypatch.setattr(auth_session, "_remote_user_sync", fake_remote)

    resolved = asyncio.run(
        resolve_session(_request({settings.SESSION_COOKIE_NAME: token}))
    )

    assert resolved.user["id"] == USER_ID
    assert resolved.clear_cookies is False
    assert seen == [token]


def test_editor_redirects_anonymous_visitors(anonymous_client):
    response = anonymous_client.get("/editor")
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_login_with_stale_cookie_keeps_new_session(anonymous_client, monkeypatch):
    import app.routers.auth as auth_module

    fresh = _token()

    class FakeAuth:
        def sign_in_with_password(self, credentials):
            return SimpleNamespace(
                user=SimpleNamespace(id=USER_ID),
                session=SimpleNamespace(
                    access_token=fresh, refresh_token="refresh-new", expires_in=3600
                ),
            )

    class FakeClient:
        auth = FakeAuth()

    monkeypatch.setattr(auth_module, "get_supabase", lambda: FakeClient())

    response = anonymous_client.post(
        "/auth/login",
        data={"email": "writer@example.com", "password": "secret"},
        cookies={settings.SESSION_COOKIE_NAME: _token(expires_in=-10)},
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/drafts"

    cookies = response.headers.get_list("set-cookie")
    session_headers = [
        cookie for cookie in cookies if cookie.startswith(f"{settings.SESSION_COOKIE_NAME}=")
    ]
    assert session_headers
    assert fresh in session_headers[-1]
    # Regression: the middleware used to append clearing headers after the
    # route's fresh ones, so the browser dropped the session it had just got.
    assert not any(
        cookie.startswith(f"{settings.SESSION_COOKIE_NAME}=") and "Max-Age=0" in cookie
        for cookie in cookies
    )


def test_logout_clears_both_session_cookies(anonymous_client):
    response = anonymous_client.post("/auth/logout")

    assert response.status_code == 303
    cookies = response.headers.get_list("set-cookie")
    assert any(
        settings.SESSION_COOKIE_NAME in cookie and "Max-Age=0" in cookie
        for cookie in cookies
    )
    assert any(
        settings.REFRESH_COOKIE_NAME in cookie and "Max-Age=0" in cookie
        for cookie in cookies
    )


def test_security_headers_present(anonymous_client):
    response = anonymous_client.get("/login")
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]
    assert "https://*.supabase.co" in response.headers["Content-Security-Policy"]


def test_authenticated_pages_are_not_cached(client, fake_db):
    fake_db.profiles[USER_ID] = {"id": USER_ID, "consent_given": True}
    # The dependency overrides supply the fake user to the route, but the
    # middleware keys off the real session cookie resolved by SessionMiddleware.
    cookies = {settings.SESSION_COOKIE_NAME: _token()}
    for path in ("/drafts", "/editor"):
        response = client.get(path, cookies=cookies)
        assert response.status_code == 200
        assert response.headers["Cache-Control"] == "no-store"


def test_reset_page_is_not_cached(anonymous_client):
    response = anonymous_client.get("/reset-password")
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"


def test_public_landing_stays_cacheable(anonymous_client):
    response = anonymous_client.get("/")
    assert response.status_code == 200
    assert "no-store" not in response.headers.get("Cache-Control", "")


def test_reset_password_with_token_hash(anonymous_client, monkeypatch):
    """The OTP path exchanges the hash server-side, then changes the password."""
    import app.routers.auth as auth_module

    calls = []

    class FakeAuth:
        def verify_otp(self, params):
            calls.append(("verify_otp", params))
            return SimpleNamespace(
                session=SimpleNamespace(
                    access_token="hash-access",
                    refresh_token="hash-refresh",
                    expires_in=3600,
                )
            )

        def set_session(self, access, refresh):
            calls.append(("set_session", access, refresh))

        def update_user(self, attrs):
            calls.append(("update_user", attrs))

    class FakeClient:
        auth = FakeAuth()

    monkeypatch.setattr(auth_module, "get_supabase", lambda: FakeClient())

    response = anonymous_client.post(
        "/auth/reset-password",
        data={
            "token_hash": "one-time-hash",
            "password": "newpassword",
            "confirm_password": "newpassword",
        },
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/login?reset=1"
    assert calls[0] == ("verify_otp", {"token_hash": "one-time-hash", "type": "recovery"})
    assert ("set_session", "hash-access", "hash-refresh") in calls
    assert ("update_user", {"password": "newpassword"}) in calls


def test_reset_password_with_implicit_tokens(anonymous_client, monkeypatch):
    """auth.js-filled tokens must keep working (implicit and PKCE paths)."""
    import app.routers.auth as auth_module

    calls = []

    class FakeAuth:
        def set_session(self, access, refresh):
            calls.append(("set_session", access, refresh))

        def update_user(self, attrs):
            calls.append(("update_user", attrs))

    class FakeClient:
        auth = FakeAuth()

    monkeypatch.setattr(auth_module, "get_supabase", lambda: FakeClient())

    response = anonymous_client.post(
        "/auth/reset-password",
        data={
            "access_token": "access",
            "refresh_token": "refresh",
            "password": "newpassword",
            "confirm_password": "newpassword",
        },
    )

    assert response.status_code == 303
    assert ("set_session", "access", "refresh") in calls
    assert ("update_user", {"password": "newpassword"}) in calls



def test_login_network_error_redirects_offline(anonymous_client, monkeypatch):
    """A connection failure must not masquerade as bad credentials."""
    import app.routers.auth as auth_module

    class FakeAuth:
        def sign_in_with_password(self, credentials):
            raise ConnectionError("httpx.ConnectError: [Errno 111] Connection refused")

    class FakeClient:
        auth = FakeAuth()

    monkeypatch.setattr(auth_module, "get_supabase", lambda: FakeClient())

    response = anonymous_client.post(
        "/auth/login",
        data={"email": "writer@example.com", "password": "secret"},
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/offline"


def _fake_verify_client(monkeypatch, session_tokens, verify_error=None):
    """Patch Supabase auth.verify_otp and return the captured params."""
    import app.routers.auth as auth_module

    seen = {}

    class FakeAuth:
        def verify_otp(self, params):
            seen["params"] = params
            if verify_error:
                raise verify_error
            return SimpleNamespace(session=session_tokens)

    class FakeClient:
        auth = FakeAuth()

    monkeypatch.setattr(auth_module, "get_supabase", lambda: FakeClient())
    return seen


def _confirm_session():
    return SimpleNamespace(
        access_token=_token(),
        refresh_token="refresh-confirm",
        expires_in=3600,
    )


def test_confirm_email_signup_sets_session_cookies(anonymous_client, monkeypatch):
    seen = _fake_verify_client(monkeypatch, _confirm_session())

    response = anonymous_client.get("/auth/confirm?token_hash=abc&type=signup")

    assert response.status_code == 303
    assert response.headers["location"] == "/drafts"
    assert seen["params"] == {"token_hash": "abc", "type": "signup"}
    cookies = response.headers.get_list("set-cookie")
    assert any(settings.SESSION_COOKIE_NAME in c for c in cookies)
    assert any(settings.REFRESH_COOKIE_NAME in c for c in cookies)


def test_confirm_email_magic_link_goes_to_drafts(anonymous_client, monkeypatch):
    _fake_verify_client(monkeypatch, _confirm_session())

    response = anonymous_client.get("/auth/confirm?token_hash=abc&type=magiclink")

    assert response.status_code == 303
    assert response.headers["location"] == "/drafts"


def test_confirm_email_accepts_email_type(anonymous_client, monkeypatch):
    """gotrue's EmailOtpType includes 'email'; verify_otp accepts it too."""
    seen = _fake_verify_client(monkeypatch, _confirm_session())

    response = anonymous_client.get("/auth/confirm?token_hash=abc&type=email")

    assert response.status_code == 303
    assert response.headers["location"] == "/drafts"
    assert seen["params"] == {"token_hash": "abc", "type": "email"}


def test_confirm_email_rejects_unknown_type(anonymous_client, monkeypatch):
    seen = _fake_verify_client(monkeypatch, _confirm_session())

    response = anonymous_client.get("/auth/confirm?token_hash=abc&type=bogus")

    assert response.status_code == 303
    assert "/login?error=" in response.headers["location"]
    assert "params" not in seen


def test_confirm_email_rejects_expired_hash(anonymous_client, monkeypatch):
    _fake_verify_client(monkeypatch, None)

    response = anonymous_client.get("/auth/confirm?token_hash=abc&type=signup")

    assert response.status_code == 303
    assert "/login?error=" in response.headers["location"]
    assert "set-cookie" not in response.headers


def test_confirm_email_network_error_goes_offline(anonymous_client, monkeypatch):
    error = ConnectionError("httpx.ConnectError: connection refused")
    _fake_verify_client(monkeypatch, None, verify_error=error)

    response = anonymous_client.get("/auth/confirm?token_hash=abc&type=signup")

    assert response.status_code == 303
    assert response.headers["location"] == "/offline"


def test_confirm_email_ignores_stale_session_cookie(
    anonymous_client, monkeypatch
):
    """Middleware must not append clear-cookie headers after a fresh login."""
    _fake_verify_client(monkeypatch, _confirm_session())

    response = anonymous_client.get(
        "/auth/confirm?token_hash=abc&type=signup",
        cookies={settings.SESSION_COOKIE_NAME: _token(expires_in=-10)},
    )

    assert response.status_code == 303
    session_cookies = [
        c for c in response.headers.get_list("set-cookie")
        if c.startswith(f"{settings.SESSION_COOKIE_NAME}=")
    ]
    assert session_cookies
    assert not any("Max-Age=0" in c for c in session_cookies)


def test_forgot_password_uses_configured_base_url(anonymous_client, monkeypatch):
    """The APP_BASE_URL override must win over a spoofed Host header."""
    import app.routers.auth as auth_module

    captured = {}

    class FakeAuth:
        def reset_password_for_email(self, email, options):
            captured["redirect_to"] = options["redirect_to"]

    class FakeClient:
        auth = FakeAuth()

    monkeypatch.setattr(settings, "APP_BASE_URL", "https://penman.example.com")
    monkeypatch.setattr(auth_module, "get_supabase", lambda: FakeClient())

    response = anonymous_client.post(
        "/auth/forgot-password",
        data={"email": "writer@example.com"},
        headers={"Host": "evil.example.com"},
    )

    assert response.status_code == 200
    assert captured["redirect_to"] == "https://penman.example.com/reset-password"


def test_reset_same_password_is_actionable(anonymous_client, monkeypatch):
    """A rejected password keeps the session so the user can just retry."""
    import app.routers.auth as auth_module

    attempts = []

    class FakeAuth:
        def verify_otp(self, params):
            return SimpleNamespace(
                session=SimpleNamespace(
                    access_token="session-access",
                    refresh_token="session-refresh",
                    expires_in=3600,
                )
            )

        def set_session(self, access, refresh):
            pass

        def update_user(self, attrs):
            attempts.append(attrs["password"])
            if len(attempts) == 1:
                raise RuntimeError("New password should be different from the old password.")

    class FakeClient:
        auth = FakeAuth()

    monkeypatch.setattr(auth_module, "get_supabase", lambda: FakeClient())

    response = anonymous_client.post(
        "/auth/reset-password",
        data={
            "token_hash": "one-time-hash",
            "password": "oldpassword",
            "confirm_password": "oldpassword",
        },
    )

    assert response.status_code == 400
    body = html.unescape(response.text)
    expected = "Your new password can't be the same as your current one."
    assert expected in body
    assert "Please choose a different one." in body
    assert "Request a new reset link" not in body
    # The verified session is preserved; the consumed hash is not.
    assert 'id="reset-access-token" value="session-access"' in body
    assert 'id="reset-refresh-token" value="session-refresh"' in body
    assert 'id="reset-token-hash" value=""' in body

    retry = anonymous_client.post(
        "/auth/reset-password",
        data={
            "access_token": "session-access",
            "refresh_token": "session-refresh",
            "password": "brandnewpassword",
            "confirm_password": "brandnewpassword",
        },
    )
    assert retry.status_code == 303
    assert retry.headers["location"] == "/login?reset=1"
    assert attempts == ["oldpassword", "brandnewpassword"]


def test_reset_mismatch_preserves_the_link(anonymous_client):
    response = anonymous_client.post(
        "/auth/reset-password",
        data={
            "token_hash": "one-time-hash",
            "password": "newpassword1",
            "confirm_password": "newpassword2",
        },
    )

    assert response.status_code == 400
    assert "Passwords do not match." in html.unescape(response.text)
    assert 'id="reset-token-hash" value="one-time-hash"' in response.text


def test_reset_mismatch_preserves_implicit_tokens(anonymous_client):
    """A password typo must not force an implicit/PKCE user to request a new mail."""
    response = anonymous_client.post(
        "/auth/reset-password",
        data={
            "access_token": "implicit-access",
            "refresh_token": "implicit-refresh",
            "password": "newpassword1",
            "confirm_password": "newpassword2",
        },
    )

    assert response.status_code == 400
    assert 'id="reset-access-token" value="implicit-access"' in response.text
    assert 'id="reset-refresh-token" value="implicit-refresh"' in response.text
    # auth.js needs the config on every render, not just on the initial GET.
    assert 'data-url="https://test.supabase.co"' in response.text
    assert 'data-anon-key="test-anon-key"' in response.text


def test_reset_expired_link_asks_for_a_new_one(anonymous_client, monkeypatch):
    import app.routers.auth as auth_module

    class FakeAuth:
        def verify_otp(self, params):
            raise RuntimeError("Invalid token")

    class FakeClient:
        auth = FakeAuth()

    monkeypatch.setattr(auth_module, "get_supabase", lambda: FakeClient())

    response = anonymous_client.post(
        "/auth/reset-password",
        data={
            "token_hash": "expired-hash",
            "password": "newpassword",
            "confirm_password": "newpassword",
        },
    )

    assert response.status_code == 400
    assert "This reset link has expired. Request a new one." in html.unescape(response.text)
    assert 'id="reset-token-hash" value=""' in response.text
    assert 'id="reset-access-token" value=""' in response.text
