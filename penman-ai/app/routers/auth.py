import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

from app.auth_session import SessionTokens, clear_session_cookies, set_session_cookies
from app.config import settings
from app.db.supabase_client import get_supabase
from app.dependencies import get_current_user, get_user_supabase
from app.templating import templates

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])


def _friendly_auth_error(exc: Exception) -> str:
    """Map a Supabase auth error to a short, user-facing message."""
    message = str(exc).lower()
    if "already registered" in message or "already exists" in message:
        return "An account with this email already exists. Try logging in."
    if "password" in message or "weak" in message:
        return "Choose a stronger password of at least 8 characters."
    if "rate limit" in message or "too many" in message:
        return "Too many attempts. Please wait a moment and try again."
    if "email" in message and "invalid" in message:
        return "That email address looks invalid. Check it and try again."
    return "Registration failed. Please check your details and try again."


def _tokens_from_session(session) -> SessionTokens | None:
    if not session or not session.access_token:
        return None
    return SessionTokens(
        access_token=session.access_token,
        refresh_token=session.refresh_token,
        expires_in=int(session.expires_in or 3600),
    )


@router.post("/register")
async def register(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    display_name: str = Form(""),
):
    email = email.strip()
    display_name = display_name.strip()[:80]
    try:
        client = get_supabase()
        result = client.auth.sign_up({
            "email": email,
            "password": password,
            "options": {"data": {"display_name": display_name}},
        })
        if not result.user:
            from urllib.parse import quote
            return RedirectResponse(
                url="/register?error=" + quote("Registration failed. Try again."),
                status_code=303,
            )

        tokens = _tokens_from_session(result.session)
        if tokens:
            response = RedirectResponse(url="/consent", status_code=303)
            set_session_cookies(response, tokens)
            return response

        # Email confirmation required — no session yet.
        return templates.TemplateResponse(
            "login.html",
            {
                "request": request,
                "info": "Check your email to confirm your account, then log in.",
            },
        )
    except Exception as exc:
        logger.exception("Registration failed for %s", email)
        from urllib.parse import quote
        return RedirectResponse(
            url="/register?error=" + quote(_friendly_auth_error(exc)),
            status_code=303,
        )


@router.post("/login")
async def login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
):
    try:
        client = get_supabase()
        result = client.auth.sign_in_with_password({
            "email": email.strip(),
            "password": password,
        })
        tokens = _tokens_from_session(result.session)
        if not tokens:
            from urllib.parse import quote
            return RedirectResponse(
                url="/login?error=" + quote("Invalid credentials."),
                status_code=303,
            )

        response = RedirectResponse(url="/drafts", status_code=303)
        set_session_cookies(response, tokens)
        return response
    except Exception:
        logger.info("Login failed for %s", email, exc_info=True)
        from urllib.parse import quote
        return RedirectResponse(
            url="/login?error=" + quote("Invalid email or password."),
            status_code=303,
        )


@router.post("/forgot-password")
async def forgot_password(request: Request, email: str = Form(...)):
    """Send a Supabase recovery email with a link back to /reset-password."""
    email = email.strip()
    redirect_to = str(request.base_url).rstrip("/") + "/reset-password"
    try:
        client = get_supabase()
        client.auth.reset_password_for_email(
            email, {"redirect_to": redirect_to}
        )
    except Exception:
        # Never reveal whether the address exists.
        logger.info("Password reset requested for %s", email, exc_info=True)
    return templates.TemplateResponse(
        "forgot_password.html",
        {
            "request": request,
            "info": "If that email has an account, a reset link is on its way.",
        },
    )


@router.post("/reset-password")
async def reset_password(
    request: Request,
    password: str = Form(...),
    confirm_password: str = Form(...),
    access_token: str = Form(""),
    refresh_token: str = Form(""),
):
    """Apply a new password using the recovery tokens from the email link."""
    if password != confirm_password:
        return templates.TemplateResponse(
            "reset_password.html",
            {"request": request, "error": "Passwords do not match."},
            status_code=400,
        )
    if not access_token or not refresh_token:
        return templates.TemplateResponse(
            "reset_password.html",
            {
                "request": request,
                "error": "Your reset link is missing or invalid. Request a new one.",
            },
            status_code=400,
        )
    try:
        client = get_supabase()
        client.auth.set_session(access_token, refresh_token)
        client.auth.update_user({"password": password})
    except Exception:
        logger.info("Password reset failed", exc_info=True)
        return templates.TemplateResponse(
            "reset_password.html",
            {
                "request": request,
                "error": "Could not change the password. Request a new reset link.",
            },
            status_code=400,
        )
    return RedirectResponse(url="/login?reset=1", status_code=303)


@router.post("/logout")
async def logout(request: Request):
    access_token = request.cookies.get(settings.SESSION_COOKIE_NAME)

    # Best-effort revoke of this session's refresh token. Access tokens cannot
    # be revoked individually, they simply expire.
    if access_token:
        try:
            client = get_supabase()
            client.auth.admin.sign_out(access_token, "local")
        except Exception:
            logger.warning("Could not revoke session during logout", exc_info=True)

    response = RedirectResponse(url="/", status_code=303)
    clear_session_cookies(response)
    return response


@router.post("/consent")
async def submit_consent(
    request: Request,
    privacy_consent: str = Form(None),
    academic_consent: str = Form(None),
    user: dict = Depends(get_current_user),
    client=Depends(get_user_supabase),
):
    if not (privacy_consent and academic_consent):
        return templates.TemplateResponse(
            "consent.html",
            {
                "request": request,
                "user": user,
                "error": "You must accept both agreements to continue.",
            },
            status_code=400,
        )

    try:
        payload = {
            "consent_given": True,
            "consent_at": datetime.now(UTC).isoformat(),
        }
        res = (
            client.table("profiles")
            .update(payload)
            .eq("id", user["id"])
            .execute()
        )

        if not res.data:
            # No matching profile row (for example the signup trigger is
            # missing). Redirecting here would loop /consent → /editor → /consent
            # forever, because the flag was never stored.
            logger.warning("No profile row for %s; creating one", user["id"])
            res = (
                client.table("profiles")
                .upsert({"id": user["id"], **payload})
                .execute()
            )
        if not res.data:
            raise RuntimeError("Consent was not persisted")

        return RedirectResponse(url="/drafts", status_code=303)
    except Exception:
        logger.exception("Failed to record consent for %s", user["id"])
        return templates.TemplateResponse(
            "consent.html",
            {
                "request": request,
                "user": user,
                "error": "We could not save your consent. Please try again.",
            },
            status_code=500,
        )
