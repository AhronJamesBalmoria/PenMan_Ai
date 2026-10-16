from fastapi import Request, Depends
from app.config import settings
from app.db.supabase_client import get_supabase, get_supabase_with_token
from app.exceptions import RedirectException


def _is_network_error(err: Exception) -> bool:
    """Detect network-level failures (as opposed to auth failures)."""
    msg = str(err).lower()
    markers = [
        "connect", "timeout", "network", "getaddrinfo",
        "name or service", "no route to host", "max retries",
        "connection refused", "unreachable", "temporary failure",
        "server disconnected", "httpx.",
    ]
    return any(m in msg for m in markers)


def get_current_user(request: Request) -> dict:
    token = request.cookies.get(settings.SESSION_COOKIE_NAME)
    if not token:
        raise RedirectException("/login")

    try:
        client = get_supabase_with_token(token)
        user_response = client.auth.get_user(token)
        if not user_response or not user_response.user:
            raise RedirectException("/login")
        return {
            "id": user_response.user.id,
            "email": user_response.user.email,
            "access_token": token,
        }
    except RedirectException:
        raise
    except Exception as e:
        # If it's a network issue, don't log the user out — send them to /offline
        if _is_network_error(e):
            raise RedirectException("/offline")
        # Otherwise treat as invalid session
        raise RedirectException("/login")


def get_optional_user(request: Request) -> dict | None:
    try:
        return get_current_user(request)
    except RedirectException:
        return None


def get_user_supabase(user: dict = Depends(get_current_user)):
    return get_supabase_with_token(user["access_token"])