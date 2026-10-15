from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.dependencies import get_current_user, get_optional_user
from app.db.supabase_client import get_supabase_with_token

router = APIRouter(tags=["pages"])
templates = Jinja2Templates(directory="app/templates")


@router.get("/", response_class=HTMLResponse)
async def landing(request: Request):
    user = get_optional_user(request)
    if user:
        return RedirectResponse(url="/editor", status_code=303)
    return templates.TemplateResponse("landing.html", {"request": request})


@router.get("/register", response_class=HTMLResponse)
async def register_page(request: Request):
    return templates.TemplateResponse("register.html", {"request": request})


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})


@router.get("/consent", response_class=HTMLResponse)
async def consent_page(request: Request, user: dict = Depends(get_current_user)):
    # If already consented, skip to editor
    try:
        client = get_supabase_with_token(user["access_token"])
        res = client.table("profiles").select("consent_given").eq("id", user["id"]).single().execute()
        if res.data and res.data.get("consent_given"):
            return RedirectResponse(url="/editor", status_code=303)
    except Exception:
        pass
    return templates.TemplateResponse("consent.html", {"request": request, "user": user})


@router.get("/editor", response_class=HTMLResponse)
async def editor_page(request: Request, user: dict = Depends(get_current_user)):
    # Check consent
    try:
        client = get_supabase_with_token(user["access_token"])
        res = client.table("profiles").select("consent_given").eq("id", user["id"]).single().execute()
        if not res.data or not res.data.get("consent_given"):
            return RedirectResponse(url="/consent", status_code=303)
    except Exception:
        return RedirectResponse(url="/consent", status_code=303)

    return templates.TemplateResponse("editor.html", {"request": request, "user": user})