from fastapi import APIRouter, Request, Form, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from datetime import datetime, timezone

from app.config import settings
from app.db.supabase_client import get_supabase, get_supabase_with_token
from app.dependencies import get_current_user

router = APIRouter(prefix="/auth", tags=["auth"])
templates = Jinja2Templates(directory="app/templates")


@router.post("/register")
async def register(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    display_name: str = Form(""),
):
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

        # If email confirmation is disabled in Supabase, session is present
        if result.session:
            response = RedirectResponse(url="/consent", status_code=303)
            response.set_cookie(
                key=settings.SESSION_COOKIE_NAME,
                value=result.session.access_token,
                httponly=True,
                samesite="lax",
                max_age=60 * 60 * 24 * 7,
            )
            return response

        # Else ask user to confirm email
        return templates.TemplateResponse(
            "login.html",
            {
                "request": request,
                "info": "Check your email to confirm your account, then log in.",
            },
        )
    except Exception as e:
        return templates.TemplateResponse(
            "register.html",
            {"request": request, "error": str(e)},
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
            "email": email,
            "password": password,
        })
        if not result.session:
            return templates.TemplateResponse(
                "login.html",
                {"request": request, "error": "Invalid credentials."},
                status_code=401,
            )

        response = RedirectResponse(url="/editor", status_code=303)
        response.set_cookie(
            key=settings.SESSION_COOKIE_NAME,
            value=result.session.access_token,
            httponly=True,
            samesite="lax",
            max_age=60 * 60 * 24 * 7,
        )
        return response
    except Exception as e:
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": "Invalid email or password."},
            status_code=401,
        )


@router.post("/logout")
async def logout():
    response = RedirectResponse(url="/", status_code=303)
    response.delete_cookie(settings.SESSION_COOKIE_NAME)
    return response


@router.post("/consent")
async def submit_consent(
    request: Request,
    privacy_consent: str = Form(None),
    academic_consent: str = Form(None),
    user: dict = Depends(get_current_user),
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
        client = get_supabase_with_token(user["access_token"])
        client.table("profiles").update({
            "consent_given": True,
            "consent_at": datetime.now(timezone.utc).isoformat(),
        }).eq("id", user["id"]).execute()

        return RedirectResponse(url="/editor", status_code=303)
    except Exception as e:
        return templates.TemplateResponse(
            "consent.html",
            {"request": request, "user": user, "error": f"Error: {e}"},
            status_code=500,
        )