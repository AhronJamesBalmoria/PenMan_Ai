from fastapi import Depends, Request

from app.db.supabase_client import get_supabase_with_token
from app.exceptions import RedirectException


def get_current_user(request: Request) -> dict:
    """Return the session user resolved by SessionMiddleware, or redirect."""
    user = getattr(request.state, "user", None)
    if user:
        return user
    if getattr(request.state, "auth_offline", False):
        raise RedirectException("/offline")
    raise RedirectException("/login")


def get_optional_user(request: Request) -> dict | None:
    return getattr(request.state, "user", None)


def get_user_supabase(user: dict = Depends(get_current_user)):
    """Supabase client authenticated as the current user."""
    return get_supabase_with_token(user["access_token"])
