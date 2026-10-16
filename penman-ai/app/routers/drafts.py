from fastapi import APIRouter, Request, Form, UploadFile, File, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from app.dependencies import get_current_user, get_user_supabase
from app.services.file_parser import extract_text

router = APIRouter(tags=["drafts"])
templates = Jinja2Templates(directory="app/templates")


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
    payload = {
        "title": title.strip() or "Untitled",
        "essay_type": essay_type,
        "content": content,
        "plain_text": plain_text,
        "word_count": word_count,
    }

    is_htmx = request.headers.get("HX-Request") == "true"

    try:
        if draft_id:
            res = (
                client.table("drafts")
                .update(payload)
                .eq("id", draft_id)
                .eq("user_id", user["id"])
                .execute()
            )
            saved = res.data[0] if res.data else None
            action = "Updated"
        else:
            payload["user_id"] = user["id"]
            res = client.table("drafts").insert(payload).execute()
            saved = res.data[0] if res.data else None
            action = "Saved"

        if not saved:
            if is_htmx:
                return HTMLResponse(
                    '<span class="text-danger">Error: no data returned</span>',
                    status_code=500,
                )
            return RedirectResponse(url="/editor", status_code=303)

        # HTMX response — inline status message
        if is_htmx:
            return HTMLResponse(
                f'<span class="text-success">✓ {action} at '
                f'<span>{saved["updated_at"][11:19]}</span>'
                f' <input type="hidden" id="new-draft-id" value="{saved["id"]}"></span>'
            )

        # Native POST fallback — redirect back to editor with draft loaded
        return RedirectResponse(
            url=f"/editor?draft_id={saved['id']}&saved=1",
            status_code=303,
        )

    except Exception as e:
        import traceback
        traceback.print_exc()
        if is_htmx:
            return HTMLResponse(
                f'<span class="text-danger">Error: {e}</span>',
                status_code=500,
            )
        raise HTTPException(status_code=500, detail=str(e))


# ---------- Delete ----------
@router.post("/drafts/{draft_id}/delete")
async def delete_draft(
    draft_id: str,
    user: dict = Depends(get_current_user),
    client=Depends(get_user_supabase),
):
    try:
        client.table("drafts").delete().eq("id", draft_id).eq("user_id", user["id"]).execute()
    except Exception:
        import traceback
        traceback.print_exc()
    return RedirectResponse(url="/drafts", status_code=303)


# ---------- File upload — returns extracted text as JSON ----------
@router.post("/api/drafts/upload")
async def upload_draft(
    file: UploadFile = File(...),
    user: dict = Depends(get_current_user),
):
    if not file.filename:
        raise HTTPException(status_code=400, detail="No file provided")

    try:
        contents = await file.read()
        text = extract_text(file.filename, contents)
        return JSONResponse({"text": text, "filename": file.filename})
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to parse file: {e}")