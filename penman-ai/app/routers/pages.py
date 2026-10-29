import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.dependencies import get_current_user, get_optional_user, get_user_supabase
from app.templating import templates

logger = logging.getLogger(__name__)

router = APIRouter(tags=["pages"])


@router.get("/offline", response_class=HTMLResponse)
async def offline_page(request: Request):
    return templates.TemplateResponse(
        "offline.html",
        {"request": request, "user": get_optional_user(request)},
    )


def _check_consent(client, user_id: str) -> bool:
    try:
        res = (
            client.table("profiles")
            .select("consent_given")
            .eq("id", user_id)
            .single()
            .execute()
        )
        return bool(res.data and res.data.get("consent_given"))
    except Exception:
        logger.warning("Could not read consent for %s", user_id, exc_info=True)
        return False


@router.get("/", response_class=HTMLResponse)
async def landing(request: Request):
    user = get_optional_user(request)
    if user:
        return RedirectResponse(url="/editor", status_code=303)
    return templates.TemplateResponse(
        "landing.html", {"request": request, "user": user}
    )


@router.get("/register", response_class=HTMLResponse)
async def register_page(request: Request):
    return templates.TemplateResponse("register.html", {"request": request})


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})


@router.get("/consent", response_class=HTMLResponse)
async def consent_page(
    request: Request,
    user: dict = Depends(get_current_user),
    client=Depends(get_user_supabase),
):
    try:
        if _check_consent(client, user["id"]):
            return RedirectResponse(url="/editor", status_code=303)
    except Exception:
        logger.warning("Consent check failed for %s", user["id"], exc_info=True)
    return templates.TemplateResponse(
        "consent.html", {"request": request, "user": user}
    )


@router.get("/editor", response_class=HTMLResponse)
async def editor_page(
    request: Request,
    draft_id: str | None = None,
    saved: int = 0,
    user: dict = Depends(get_current_user),
    client=Depends(get_user_supabase),
):
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
            logger.info("Draft %s not found for %s", draft_id, user["id"])
            draft = None

    return templates.TemplateResponse(
        "editor.html",
        {
            "request": request,
            "user": user,
            "draft": draft,
            "saved": bool(saved),
        },
    )


@router.get("/drafts", response_class=HTMLResponse)
async def drafts_page(
    request: Request,
    error: str | None = None,
    deleted: int = 0,
    user: dict = Depends(get_current_user),
    client=Depends(get_user_supabase),
):
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
        logger.exception("Could not list drafts for %s", user["id"])
        drafts = []
        error = error or "Could not load your drafts. Please refresh."

    return templates.TemplateResponse(
        "drafts.html",
        {
            "request": request,
            "user": user,
            "drafts": drafts,
            "error": error,
            "deleted": bool(deleted),
        },
    )


@router.get("/health")
async def health_check():
    """Simple health check endpoint for monitoring and deployment."""
    return {"status": "ok", "app": "Penman AI", "version": "0.2.0"}
