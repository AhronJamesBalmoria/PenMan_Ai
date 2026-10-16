from fastapi import APIRouter, Request, Form, Depends
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.dependencies import get_current_user
from app.services.grammar import check_grammar

router = APIRouter(prefix="/api/analyze", tags=["analysis"])
templates = Jinja2Templates(directory="app/templates")


@router.post("/grammar", response_class=HTMLResponse)
async def analyze_grammar(
    request: Request,
    plain_text: str = Form(""),
    user: dict = Depends(get_current_user),
):
    result = await check_grammar(plain_text)

    # Group matches by category for a cleaner UI
    matches = result["matches"]
    for m in matches:
        rule = m.get("rule", {}) or {}
        m["category_id"] = rule.get("category", {}).get("id", "OTHER")
        m["category_name"] = rule.get("category", {}).get("name", "Other")
        m["issue_type"] = rule.get("issueType", "uncategorized")
        m["replacements"] = [r.get("value", "") for r in (m.get("replacements") or [])][:3]

    return templates.TemplateResponse(
        "partials/grammar_results.html",
        {
            "request": request,
            "matches": matches,
            "error": result["error"],
            "truncated": result["truncated"],
            "count": len(matches),
        },
    )