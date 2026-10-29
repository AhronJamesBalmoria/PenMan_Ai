"""Custom middleware: session resolution and security headers."""
from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

from app.auth_session import (
    clear_session_cookies,
    resolve_session,
    set_session_cookies,
)

SKIP_PREFIXES = ("/static/",)
SKIP_PATHS = {"/health", "/favicon.ico"}

# Auth POSTs manage the session cookies themselves. Rewriting cookies here
# would append the "clear" headers *after* the fresh ones the route just set,
# and the browser keeps the last write — silently logging the user back out.
COOKIE_OWNED_ROUTES = {
    ("POST", "/auth/login"),
    ("POST", "/auth/register"),
    ("POST", "/auth/logout"),
}

CONTENT_SECURITY_POLICY = "; ".join(
    [
        "default-src 'self'",
        "script-src 'self' https://cdn.jsdelivr.net https://unpkg.com",
        "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net",
        "img-src 'self' data:",
        "font-src 'self' data: https://cdn.jsdelivr.net",
        "connect-src 'self'",
        "frame-ancestors 'none'",
        "base-uri 'self'",
        "form-action 'self'",
    ]
)


class SessionMiddleware(BaseHTTPMiddleware):
    """Resolve the current user once per request and persist refreshed tokens."""

    async def dispatch(self, request: Request, call_next):
        request.state.user = None
        request.state.auth_offline = False

        path = request.url.path
        if path in SKIP_PATHS or path.startswith(SKIP_PREFIXES):
            return await call_next(request)
        if (request.method, path) in COOKIE_OWNED_ROUTES:
            return await call_next(request)

        resolved = await resolve_session(request)
        request.state.user = resolved.user
        request.state.auth_offline = resolved.offline

        response = await call_next(request)

        if resolved.clear_cookies:
            clear_session_cookies(response)
        if resolved.new_tokens:
            set_session_cookies(response, resolved.new_tokens)
        return response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Attach baseline security headers to every response."""

    def __init__(self, app: ASGIApp, *, hsts: bool = False) -> None:
        super().__init__(app)
        self.hsts = hsts

    async def dispatch(self, request: Request, call_next):
        response: Response = await call_next(request)
        headers = response.headers
        headers.setdefault("Content-Security-Policy", CONTENT_SECURITY_POLICY)
        headers.setdefault("X-Content-Type-Options", "nosniff")
        headers.setdefault("X-Frame-Options", "DENY")
        headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        if self.hsts:
            headers.setdefault(
                "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
            )
        return response
