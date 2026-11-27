import logging
from datetime import UTC, datetime
from urllib.parse import quote

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse
from starlette.concurrency import run_in_threadpool

from app.auth_session import (
    SessionTokens,
    clear_session_cookies,
    is_network_error,
    set_session_cookies,
)
from app.config import settings
from app.db.supabase_client import get_supabase
from app.templating import templates

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

SAME_PASSWORD_ERROR = (
    "Your new password can't be the same as your current one. "
    "Please choose a different one."
)

# Supabase token_hash email links carry a one-time hash plus the flow type.
# "email" is a valid gotrue EmailOtpType too; rejecting it breaks some
# confirmation links even though verify_otp would accept them.
CONFIRM_TYPES = {
    "signup", "magiclink", "invite", "email_change", "recovery", "email"
}


def _auth_error_redirect(exc: Exception, page: str, message: str):
    """Network failures go to the offline page; real auth errors get a message."""
    if is_network_error(exc):
        logger.info("Auth request failed due to network error: %s", exc)
        return RedirectResponse(url="/offline", status_code=303)
    return RedirectResponse(
        url=f"{page}?error={quote(message)}" if message else page,
        status_code=303,
    )


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


def _friendly_reset_error(exc: Exception) -> tuple[str, str]:
    """Return a user-facing reset message and its category.

    Categories: ``password`` (session still usable), ``link`` (link is dead),
    or ``unknown``. A rejected new password leaves the verified session
    intact, so the user can simply pick a different password.
    """
    message = str(exc).lower()
    if (
        "different from the old password" in message
        or "same as the old password" in message
        or "same as your current password" in message
    ):
        return SAME_PASSWORD_ERROR, "password"
    if "weak" in message or ("password" in message and "short" in message):
        return "Choose a stronger password (at least 8 characters).", "password"
    if any(marker in message for marker in ("expired", "invalid token", "not found")):
        return "This reset link has expired. Request a new one.", "link"
    return "Could not change the password. Request a new reset link.", "unknown"


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
    terms_agreed: str = Form(""),
):
    email = email.strip()
    display_name = display_name.strip()[:80]
    if not terms_agreed:
        # The checkbox in register.html is only a hint; acceptance must be
        # enforced server-side and is recorded with the signup metadata.
        return RedirectResponse(
            url="/register?error="
            + quote("You must accept the Terms of Service and Privacy Notice."),
            status_code=303,
        )
    try:
        client = get_supabase()
        result = await run_in_threadpool(
            client.auth.sign_up,
            {
                "email": email,
                "password": password,
                "options": {
                    "data": {
                        "display_name": display_name,
                        "terms_accepted_at": datetime.now(UTC).isoformat(),
                    }
                },
            },
        )
        if not result.user:
            return RedirectResponse(
                url="/register?error=" + quote("Registration failed. Try again."),
                status_code=303,
            )

        tokens = _tokens_from_session(result.session)
        if tokens:
            response = RedirectResponse(url="/drafts", status_code=303)
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
        return _auth_error_redirect(
            exc, "/register", _friendly_auth_error(exc)
        )


@router.post("/login")
async def login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
):
    try:
        client = get_supabase()
        result = await run_in_threadpool(
            client.auth.sign_in_with_password,
            {"email": email.strip(), "password": password},
        )
        tokens = _tokens_from_session(result.session)
        if not tokens:
            return RedirectResponse(
                url="/login?error=" + quote("Invalid credentials."),
                status_code=303,
            )

        response = RedirectResponse(url="/drafts", status_code=303)
        set_session_cookies(response, tokens)
        return response
    except Exception as exc:
        logger.info("Login failed for %s", email, exc_info=True)
        return _auth_error_redirect(
            exc, "/login", "Invalid email or password."
        )


@router.post("/forgot-password")
async def forgot_password(request: Request, email: str = Form(...)):
    """Send a Supabase recovery email with a link back to /reset-password."""
    email = email.strip()
    base_url = settings.APP_BASE_URL or str(request.base_url).rstrip("/")
    redirect_to = base_url + "/reset-password"
    try:
        client = get_supabase()
        await run_in_threadpool(
            client.auth.reset_password_for_email,
            email,
            {"redirect_to": redirect_to},
        )
    except Exception as exc:
        # Network errors mean the request never reached Supabase.
        if is_network_error(exc):
            logger.info("Reset request failed due to network error: %s", exc)
            return RedirectResponse(url="/offline", status_code=303)
        # Never reveal whether the address exists for real auth errors.
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
    token_hash: str = Form(""),
    reset_type: str = Form("recovery"),
):
    """Apply a new password using the recovery tokens/hash from the email link."""
    def _render_error(
        message: str,
        session_tokens: SessionTokens | None = None,
        clear_link: bool = False,
    ):
        """Re-render with credentials so the user can retry without a new mail."""
        if session_tokens is not None:
            # A verified session survives a rejected password, but the
            # one-time token_hash has already been consumed.
            keep_access = session_tokens.access_token
            keep_refresh = session_tokens.refresh_token or ""
            keep_hash = ""
        elif clear_link:
            # Dead link: none of the supplied credentials can be reused.
            keep_access = keep_refresh = keep_hash = ""
        else:
            # Validation failed before any token was exchanged. Keep exactly
            # what the form submitted — implicit/PKCE users would otherwise
            # lose their tokens after a simple password mismatch.
            keep_access = access_token
            keep_refresh = refresh_token
            keep_hash = token_hash

        return templates.TemplateResponse(
            "reset_password.html",
            {
                "request": request,
                "error": message,
                "access_token": keep_access,
                "refresh_token": keep_refresh,
                "token_hash": keep_hash,
                "reset_type": reset_type or "recovery",
                # auth.js needs these on every render, not only on the GET:
                # the PKCE fallback reads them from #reset-config.
                "supabase_url": settings.SUPABASE_URL,
                "supabase_anon_key": settings.SUPABASE_ANON_KEY,
            },
            status_code=400,
        )

    if password != confirm_password:
        return _render_error("Passwords do not match.")
    if not token_hash and not (access_token and refresh_token):
        return _render_error(
            "Your reset link is missing or invalid. Request a new one."
        )

    session_tokens: SessionTokens | None = None
    try:
        client = get_supabase()

        def _obtain_session() -> SessionTokens | None:
            if token_hash:
                # OTP / token_hash flow: exchange the one-time hash for a
                # session on the server. Works even across devices/browsers.
                response = client.auth.verify_otp(
                    {"token_hash": token_hash, "type": reset_type or "recovery"}
                )
                return _tokens_from_session(getattr(response, "session", None))
            # Implicit / PKCE flow: auth.js already exchanged and filled the
            # access and refresh token fields.
            return SessionTokens(
                access_token=access_token,
                refresh_token=refresh_token,
            )

        session_tokens = await run_in_threadpool(_obtain_session)
        if not session_tokens:
            raise ValueError("No session returned for reset link")

        def _apply_new_password() -> None:
            client.auth.set_session(
                session_tokens.access_token, session_tokens.refresh_token
            )
            client.auth.update_user({"password": password})

        await run_in_threadpool(_apply_new_password)
    except Exception as exc:
        if is_network_error(exc):
            logger.info("Password reset failed due to network error: %s", exc)
            return RedirectResponse(url="/offline", status_code=303)
        logger.info("Password reset failed", exc_info=True)
        message, category = _friendly_reset_error(exc)
        if category == "link":
            return _render_error(message, clear_link=True)
        # Password rejections (and transient update failures) leave a verified
        # session we can reuse; retry with it rather than demanding a new mail.
        if session_tokens:
            return _render_error(message, session_tokens=session_tokens)
        return _render_error(message)
    return RedirectResponse(url="/login?reset=1", status_code=303)


@router.get("/confirm")
async def confirm_email(request: Request):
    """Verify a Supabase token_hash email link and start a real session.

    All branded Auth emails point here. Supabase never sees our app's
    redirect_to for these links; the hash is verified server-side with
    verify_otp, then the resulting tokens are put in HttpOnly cookies.
    """
    token_hash = request.query_params.get("token_hash", "")
    link_type = request.query_params.get("type", "")
    if not token_hash or link_type not in CONFIRM_TYPES:
        return RedirectResponse(
            url="/login?error="
            + quote("This link is invalid. Please request a new one."),
            status_code=303,
        )

    try:
        client = get_supabase()

        def _verify() -> SessionTokens | None:
            response = client.auth.verify_otp(
                {"token_hash": token_hash, "type": link_type}
            )
            return _tokens_from_session(getattr(response, "session", None))

        tokens = await run_in_threadpool(_verify)
        if not tokens:
            raise ValueError("No session returned for confirmation link")
    except Exception as exc:
        if is_network_error(exc):
            logger.info("Email confirmation failed due to network error: %s", exc)
            return RedirectResponse(url="/offline", status_code=303)
        logger.info("Email confirmation failed", exc_info=True)
        return RedirectResponse(
            url="/login?error="
            + quote("This link is invalid or has expired. Request a new one."),
            status_code=303,
        )

    # Consent is recorded by the registration checkbox, so all confirmed
    # links go straight to the workspace.
    response = RedirectResponse(url="/drafts", status_code=303)
    set_session_cookies(response, tokens)
    return response


@router.post("/logout")
async def logout(request: Request):
    access_token = request.cookies.get(settings.SESSION_COOKIE_NAME)

    # Best-effort revoke of this session's refresh token. Access tokens cannot
    # be revoked individually, they simply expire.
    if access_token:
        try:
            client = get_supabase()
            await run_in_threadpool(
                client.auth.admin.sign_out, access_token, "local"
            )
        except Exception:
            logger.warning("Could not revoke session during logout", exc_info=True)

    response = RedirectResponse(url="/", status_code=303)
    clear_session_cookies(response)
    return response
