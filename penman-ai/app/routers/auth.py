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
            return templates.TemplateResponse(
                "register.html",
                {"request": request, "error": "Registration failed. Try again."},
                status_code=400,
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
    except Exception:
        logger.exception("Registration failed for %s", email)
        return templates.TemplateResponse(
            "register.html",
            {
                "request": request,
                "error": "Registration failed. Check your details and try again.",
            },
            status_code=400,
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
            return templates.TemplateResponse(
                "login.html",
                {"request": request, "error": "Invalid credentials."},
                status_code=401,
            )

        response = RedirectResponse(url="/editor", status_code=303)
        set_session_cookies(response, tokens)
        return response
    except Exception:
        logger.info("Login failed for %s", email, exc_info=True)
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": "Invalid email or password."},
            status_code=401,
        )


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

        return RedirectResponse(url="/editor", status_code=303)
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
