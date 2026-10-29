"""Session resolution: local verification, refresh and middleware wiring."""
import asyncio
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
    assert response.headers["location"] == "/editor"

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
