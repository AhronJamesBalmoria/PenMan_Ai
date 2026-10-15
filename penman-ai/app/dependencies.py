from fastapi import Request, HTTPException, status, Depends
from app.config import settings
from app.db.supabase_client import get_supabase, get_supabase_with_token


def get_current_user(request: Request) -> dict:
    """
    Read the session cookie, validate against Supabase, return user dict.
    Raises 401 if not logged in.
    """
    token = request.cookies.get(settings.SESSION_COOKIE_NAME)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"Location": "/login"},
        )

    try:
        client = get_supabase_with_token(token)
        user_response = client.auth.get_user(token)
        if not user_response or not user_response.user:
            raise HTTPException(status_code=401, detail="Invalid session")
        return {
            "id": user_response.user.id,
            "email": user_response.user.email,
            "access_token": token,
        }
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired session",
            headers={"Location": "/login"},
        )


def get_optional_user(request: Request) -> dict | None:
    """Return user dict or None (for public pages)."""
    try:
        return get_current_user(request)
    except HTTPException:
        return None


def get_user_supabase(user: dict = Depends(get_current_user)):
    """Return a Supabase client scoped to the logged-in user (RLS applies)."""
    return get_supabase_with_token(user["access_token"])