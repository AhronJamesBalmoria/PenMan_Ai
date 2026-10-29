"""Session resolution: local JWT verification, refresh and cookie handling.

The access token lives in an HttpOnly cookie. When ``SUPABASE_JWT_SECRET`` is
configured the token is verified locally, so the common case costs no network
round-trip. When it expires we redeem the refresh-token cookie for a new pair.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum

import jwt
from fastapi import Request, Response
from starlette.concurrency import run_in_threadpool

from app.config import is_jwt_shaped, settings
from app.db.supabase_client import get_supabase

logger = logging.getLogger(__name__)

NETWORK_ERROR_MARKERS = (
    "connect", "timeout", "network", "getaddrinfo", "name or service",
    "no route to host", "max retries", "connection refused", "unreachable",
    "temporary failure", "server disconnected", "httpx.",
)


def is_network_error(err: Exception) -> bool:
    """Detect network-level failures (as opposed to auth failures)."""
    msg = str(err).lower()
    return any(marker in msg for marker in NETWORK_ERROR_MARKERS)


class Check(Enum):
    """Outcome of a local token check."""

    FRESH = "fresh"
    EXPIRED = "expired"
    INVALID = "invalid"
    UNVERIFIED = "unverified"


@dataclass
class SessionTokens:
    access_token: str
    refresh_token: str | None = None
    expires_in: int = 3600


@dataclass
class ResolvedSession:
    user: dict | None = None
    new_tokens: SessionTokens | None = None
    clear_cookies: bool = False
    offline: bool = False


@dataclass
class Claims:
    user_id: str
    email: str
    expires_at: int
    raw: dict = field(default_factory=dict)


def _issuer() -> str | None:
    if not settings.SUPABASE_URL:
        return None
    return f"{settings.SUPABASE_URL}/auth/v1"


def check_local(token: str) -> tuple[Check, Claims | None]:
    """Verify a Supabase HS256 access token without any network access."""
    secret = settings.SUPABASE_JWT_SECRET
    # A JWT pasted by mistake (anon/service-role key) cannot verify anything;
    # treat it as "no local verification" so sessions are checked remotely
    # instead of every valid token being rejected as forged.
    if not secret or is_jwt_shaped(secret):
        return Check.UNVERIFIED, None
    try:
        payload = jwt.decode(
            token,
            secret,
            algorithms=["HS256"],
            audience=settings.SUPABASE_JWT_AUDIENCE,
            issuer=_issuer(),
            options={"require": ["exp", "sub"]},
        )
    except jwt.ExpiredSignatureError:
        return Check.EXPIRED, None
    except jwt.InvalidAlgorithmError:
        # Project uses asymmetric keys; we cannot check those without extra deps.
        return Check.UNVERIFIED, None
    except jwt.InvalidTokenError:
        return Check.INVALID, None

    user_id = payload.get("sub")
    if not user_id:
        return Check.INVALID, None

    claims = Claims(
        user_id=user_id,
        email=payload.get("email") or "",
        expires_at=int(payload.get("exp", 0)),
        raw=payload,
    )
    if claims.expires_at <= time.time() + settings.TOKEN_REFRESH_LEEWAY:
        return Check.EXPIRED, claims
    return Check.FRESH, claims


def _user_from_token(user_id: str, email: str, access_token: str) -> dict:
    return {"id": user_id, "email": email, "access_token": access_token}


def _user_from_claims(claims: Claims, access_token: str) -> dict:
    return _user_from_token(claims.user_id, claims.email, access_token)


def _refresh_sync(refresh_token: str) -> SessionTokens | None:
    """Redeem a refresh token. Runs in a worker thread."""
    try:
        client = get_supabase()
        response = client.auth.refresh_session(refresh_token)
    except Exception as exc:
        if is_network_error(exc):
            raise
        logger.info("Refresh token rejected: %s", exc)
        return None

    session = getattr(response, "session", None)
    if not session or not session.access_token:
        return None
    return SessionTokens(
        access_token=session.access_token,
        refresh_token=session.refresh_token or refresh_token,
        expires_in=int(session.expires_in or 3600),
    )


def _remote_user_sync(access_token: str) -> dict:
    """Ask Supabase who owns the token. Raises on network failure."""
    from app.db.supabase_client import get_supabase_with_token

    client = get_supabase_with_token(access_token)
    response = client.auth.get_user(access_token)
    if not response or not response.user:
        raise ValueError("Invalid session")
    return _user_from_token(response.user.id, response.user.email or "", access_token)


async def _refresh(refresh_token: str | None) -> SessionTokens | None:
    if not refresh_token:
        return None
    try:
        return await run_in_threadpool(_refresh_sync, refresh_token)
    except Exception as exc:
        if is_network_error(exc):
            raise
        logger.info("Refresh failed: %s", exc)
        return None


async def _remote_user(access_token: str) -> dict:
    return await run_in_threadpool(_remote_user_sync, access_token)


async def _user_from_tokens(tokens: SessionTokens) -> dict | None:
    state, claims = check_local(tokens.access_token)
    if state is Check.FRESH and claims:
        return _user_from_claims(claims, tokens.access_token)
    try:
        return await _remote_user(tokens.access_token)
    except Exception as exc:
        if is_network_error(exc):
            raise
        return None


async def resolve_session(request: Request) -> ResolvedSession:
    """Resolve the logged-in user for a request, refreshing tokens if needed."""
    access_token = request.cookies.get(settings.SESSION_COOKIE_NAME)
    refresh_token = request.cookies.get(settings.REFRESH_COOKIE_NAME)

    if access_token:
        state, claims = check_local(access_token)

        if state is Check.FRESH and claims:
            return ResolvedSession(user=_user_from_claims(claims, access_token))

        if state in (Check.EXPIRED, Check.INVALID):
            if state is Check.INVALID:
                logger.info("Rejecting malformed or mis-signed session token")
            try:
                tokens = await _refresh(refresh_token)
                if tokens:
                    user = await _user_from_tokens(tokens)
                    if user:
                        return ResolvedSession(user=user, new_tokens=tokens)
            except Exception as exc:
                if is_network_error(exc):
                    logger.warning("Auth backend unreachable: %s", exc)
                    return ResolvedSession(offline=True)
            return ResolvedSession(clear_cookies=True)

        # UNVERIFIED: no secret configured, or a non-HS256 token.
        try:
            user = await _remote_user(access_token)
            return ResolvedSession(user=user)
        except Exception as exc:
            if is_network_error(exc):
                logger.warning("Auth backend unreachable: %s", exc)
                return ResolvedSession(offline=True)
            logger.info("Access token rejected by Supabase: %s", exc)

    # No usable access token — try the refresh cookie.
    if refresh_token:
        try:
            tokens = await _refresh(refresh_token)
            if tokens:
                user = await _user_from_tokens(tokens)
                if user:
                    return ResolvedSession(user=user, new_tokens=tokens)
        except Exception as exc:
            if is_network_error(exc):
                logger.warning("Auth backend unreachable: %s", exc)
                return ResolvedSession(offline=True)
        return ResolvedSession(clear_cookies=True)

    return ResolvedSession(clear_cookies=bool(access_token))


def set_session_cookies(response: Response, tokens: SessionTokens) -> None:
    max_age = max(60, int(tokens.expires_in))
    response.set_cookie(
        key=settings.SESSION_COOKIE_NAME,
        value=tokens.access_token,
        max_age=max_age,
        httponly=True,
        samesite="lax",
        secure=settings.COOKIE_SECURE,
        path="/",
    )
    if tokens.refresh_token:
        response.set_cookie(
            key=settings.REFRESH_COOKIE_NAME,
            value=tokens.refresh_token,
            max_age=settings.REFRESH_COOKIE_MAX_AGE,
            httponly=True,
            samesite="lax",
            secure=settings.COOKIE_SECURE,
            path="/",
        )


def clear_session_cookies(response: Response) -> None:
    for name in (settings.SESSION_COOKIE_NAME, settings.REFRESH_COOKIE_NAME):
        response.delete_cookie(
            name,
            path="/",
            httponly=True,
            samesite="lax",
            secure=settings.COOKIE_SECURE,
        )
