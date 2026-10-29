import html
import json
import logging
from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from starlette.concurrency import run_in_threadpool

from app.config import settings
from app.constants import normalize_essay_type
from app.dependencies import get_current_user, get_user_supabase
from app.services.file_parser import extract_text

logger = logging.getLogger(__name__)

router = APIRouter(tags=["drafts"])

MAX_WORD_COUNT = 1_000_000


def _parse_draft_id(raw: str) -> str | None:
    """Return a canonical UUID string, or None when the input is not one."""
    try:
        return str(UUID(raw))
    except (ValueError, AttributeError, TypeError):
        return None


def _error_body(message: str, status_code: int, is_htmx: bool) -> HTMLResponse:
    safe = html.escape(message)
    if is_htmx:
        return HTMLResponse(
            f'<span class="text-danger">{safe}</span>', status_code=status_code
        )
    return HTMLResponse(
        "<!doctype html><html><body>"
        f"<p>{safe}</p><p><a href=\"/editor\">Back to editor</a></p>"
        "</body></html>",
        status_code=status_code,
    )


def _timestamp(value) -> str:
    if isinstance(value, str) and len(value) >= 19:
        return value[11:19]
    return datetime.now(UTC).strftime("%H:%M:%S")


# ---------- Save (create or update) — handles both HTMX and native POST ----------
@router.post("/drafts/save", response_class=HTMLResponse)
async def save_draft(
    request: Request,
    draft_id: str = Form(""),
    title: str = Form("Untitled"),
    essay_type: str = Form("expository"),
    content: str = Form(""),
    plain_text: str = Form(""),
    word_count: int = Form(0),
    user: dict = Depends(get_current_user),
    client=Depends(get_user_supabase),
):
    is_htmx = request.headers.get("HX-Request") == "true"

    if len(content) > settings.MAX_CONTENT_CHARS:
        return _error_body("This draft is too large to save.", 413, is_htmx)
    if len(plain_text) > settings.MAX_PLAIN_TEXT_CHARS:
        return _error_body("This draft is too large to save.", 413, is_htmx)

    parsed_id = None
    if draft_id:
        parsed_id = _parse_draft_id(draft_id)
        if parsed_id is None:
            return _error_body("Invalid draft reference.", 400, is_htmx)

    payload = {
        "title": (title.strip() or "Untitled")[: settings.MAX_TITLE_CHARS],
        "essay_type": normalize_essay_type(essay_type),
        "content": content,
        "plain_text": plain_text,
        "word_count": max(0, min(word_count, MAX_WORD_COUNT)),
    }

    try:
        if parsed_id:
            # `return=representation` (the postgrest-py default) only returns
            # rows the caller may SELECT, so the RLS policy for `drafts` must
            # allow SELECT as well as UPDATE. Without it an applied update comes
            # back empty and looks like a 404; the editor then starts a new
            # draft instead of overwriting. The pages read drafts anyway, so
            # SELECT is required for the app to work at all.
            res = (
                client.table("drafts")
                .update(payload)
                .eq("id", parsed_id)
                .eq("user_id", user["id"])
                .execute()
            )
            saved = res.data[0] if res.data else None
            action = "Updated"
        else:
            payload["user_id"] = user["id"]
            # insert/update default to `return=representation` in postgrest-py,
            # so the saved row (including its id) comes back here.
            res = client.table("drafts").insert(payload).execute()
            saved = res.data[0] if res.data else None
            action = "Saved"

        if not saved:
            message = (
                "Draft not found." if parsed_id else "Could not save your draft."
            )
            return _error_body(message, 404 if parsed_id else 500, is_htmx)

        stamp = _timestamp(saved.get("updated_at"))
        draft_uuid = saved.get("id", parsed_id or "")

        if is_htmx:
            return HTMLResponse(
                f'<span class="text-success">✓ {action} at {html.escape(stamp)}</span>',
                headers={
                    "HX-Trigger": json.dumps(
                        {
                            "draftSaved": {
                                "id": draft_uuid,
                                "action": action,
                                "at": stamp,
                            }
                        }
                    )
                },
            )

        return RedirectResponse(
            url=f"/editor?draft_id={draft_uuid}&saved=1",
            status_code=303,
        )

    except Exception:
        logger.exception("Could not save draft for %s", user["id"])
        if is_htmx:
            return _error_body(
                "Could not save your draft. Please try again.", 500, is_htmx
            )
        raise HTTPException(
            status_code=500, detail="Could not save your draft."
        ) from None


# ---------- Delete ----------
@router.post("/drafts/{draft_id}/delete")
async def delete_draft(
    draft_id: str,
    user: dict = Depends(get_current_user),
    client=Depends(get_user_supabase),
):
    parsed_id = _parse_draft_id(draft_id)
    if parsed_id is None:
        return RedirectResponse(url="/drafts?error=Invalid+draft.", status_code=303)

    try:
        res = (
            client.table("drafts")
            .delete()
            .eq("id", parsed_id)
            .eq("user_id", user["id"])
            .execute()
        )
        if not res.data:
            return RedirectResponse(
                url="/drafts?error=Draft+not+found.", status_code=303
            )
    except Exception:
        logger.exception("Could not delete draft %s for %s", parsed_id, user["id"])
        return RedirectResponse(
            url="/drafts?error=Could+not+delete+that+draft.", status_code=303
        )

    return RedirectResponse(url="/drafts?deleted=1", status_code=303)


# ---------- File upload — returns extracted text as JSON ----------
@router.post("/api/drafts/upload")
async def upload_draft(
    file: UploadFile = File(...),
    user: dict = Depends(get_current_user),
):
    if not file.filename:
        raise HTTPException(status_code=400, detail="No file provided")

    contents = await file.read(settings.MAX_UPLOAD_BYTES + 1)
    if len(contents) > settings.MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=(
                "File is too large. Maximum size is "
                f"{settings.MAX_UPLOAD_BYTES // (1024 * 1024)} MB."
            ),
        )

    try:
        # python-docx is CPU-bound; keep it off the event loop.
        text = await run_in_threadpool(extract_text, file.filename, contents)
        return JSONResponse({"text": text, "filename": file.filename})
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception:
        logger.exception("Could not parse upload %s", file.filename)
        raise HTTPException(status_code=500, detail="Failed to parse file.") from None
