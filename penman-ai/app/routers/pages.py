from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.dependencies import get_current_user, get_optional_user
from app.db.supabase_client import get_supabase_with_token

router = APIRouter(tags=["pages"])
templates = Jinja2Templates(directory="app/templates")


def _check_consent(client, user_id: str) -> bool:
    try:
        res = client.table("profiles").select("consent_given").eq("id", user_id).single().execute()
        return bool(res.data and res.data.get("consent_given"))
    except Exception:
        return False


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
    try:
        client = get_supabase_with_token(user["access_token"])
        if _check_consent(client, user["id"]):
            return RedirectResponse(url="/editor", status_code=303)
    except Exception:
        pass
    return templates.TemplateResponse("consent.html", {"request": request, "user": user})


@router.get("/editor", response_class=HTMLResponse)
async def editor_page(
    request: Request,
    draft_id: str | None = None,
    user: dict = Depends(get_current_user),
):
    try:
        client = get_supabase_with_token(user["access_token"])
    except Exception:
        return RedirectResponse(url="/login", status_code=303)

    if not _check_consent(client, user["id"]):
        return RedirectResponse(url="/consent", status_code=303)

    draft = None
    if draft_id:
        try:
            dres = (
                client.table("drafts")
                .select("*")
                .eq("id", draft_id)
                .eq("user_id", user["id"])
                .single()
                .execute()
            )
            draft = dres.data
        except Exception:
            draft = None

    return templates.TemplateResponse(
        "editor.html",
        {"request": request, "user": user, "draft": draft},
    )


@router.get("/drafts", response_class=HTMLResponse)
async def drafts_page(request: Request, user: dict = Depends(get_current_user)):
    try:
        client = get_supabase_with_token(user["access_token"])
    except Exception:
        return RedirectResponse(url="/login", status_code=303)

    if not _check_consent(client, user["id"]):
        return RedirectResponse(url="/consent", status_code=303)

    try:
        dres = (
            client.table("drafts")
            .select("*")
            .eq("user_id", user["id"])
            .order("updated_at", desc=True)
            .execute()
        )
        drafts = dres.data or []
    except Exception:
        drafts = []

    return templates.TemplateResponse(
        "drafts.html",
        {"request": request, "user": user, "drafts": drafts},
    )