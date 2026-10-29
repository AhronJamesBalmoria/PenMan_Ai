from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse

from app.config import settings
from app.constants import normalize_essay_type
from app.dependencies import get_current_user
from app.services.contextual import analyze_contextual
from app.services.grammar import check_grammar
from app.services.structure import analyze_structure
from app.templating import templates

router = APIRouter(prefix="/api/analyze", tags=["analysis"])

TOO_LONG = (
    f"Essay is too long to analyze. "
    f"Please trim it to {settings.MAX_PLAIN_TEXT_CHARS:,} characters."
)


@router.post("/grammar", response_class=HTMLResponse)
async def analyze_grammar(
    request: Request,
    plain_text: str = Form(""),
    user: dict = Depends(get_current_user),
):
    if len(plain_text) > settings.MAX_PLAIN_TEXT_CHARS:
        return templates.TemplateResponse(
            "partials/grammar_results.html",
            {
                "request": request,
                "matches": [],
                "error": TOO_LONG,
                "truncated": False,
                "count": 0,
            },
        )

    result = await check_grammar(plain_text)

    # Group matches by category for a cleaner UI
    matches = result["matches"]
    for m in matches:
        rule = m.get("rule") or {}
        category = rule.get("category") or {}
        m["category_id"] = category.get("id", "OTHER")
        m["category_name"] = category.get("name", "Other")
        m["issue_type"] = rule.get("issueType", "uncategorized")
        m["replacements"] = [
            r.get("value", "") for r in (m.get("replacements") or [])
        ][:3]

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


@router.post("/structure", response_class=HTMLResponse)
async def analyze_structure_route(
    request: Request,
    plain_text: str = Form(""),
    essay_type: str = Form("expository"),
    user: dict = Depends(get_current_user),
):
    if len(plain_text) > settings.MAX_PLAIN_TEXT_CHARS:
        return templates.TemplateResponse(
            "partials/structure_results.html",
            {"request": request, "error": TOO_LONG, "truncated": False},
        )

    result = await analyze_structure(plain_text, normalize_essay_type(essay_type))
    return templates.TemplateResponse(
        "partials/structure_results.html",
        {"request": request, **result},
    )


@router.post("/contextual", response_class=HTMLResponse)
async def analyze_contextual_route(
    request: Request,
    plain_text: str = Form(""),
    essay_type: str = Form("expository"),
    user: dict = Depends(get_current_user),
):
    if len(plain_text) > settings.MAX_PLAIN_TEXT_CHARS:
        return templates.TemplateResponse(
            "partials/contextual_results.html",
            {"request": request, "error": TOO_LONG, "truncated": False},
        )

    result = await analyze_contextual(plain_text, normalize_essay_type(essay_type))
    return templates.TemplateResponse(
        "partials/contextual_results.html",
        {"request": request, **result},
    )
