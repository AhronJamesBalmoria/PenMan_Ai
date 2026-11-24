"""Single shared Jinja2 template environment."""
from datetime import datetime

from fastapi.templating import Jinja2Templates

from app.config import BASE_DIR
from app.constants import DEFAULT_ESSAY_TYPE, ESSAY_TYPES

templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
templates.env.globals["ESSAY_TYPES"] = ESSAY_TYPES
templates.env.globals["DEFAULT_ESSAY_TYPE"] = DEFAULT_ESSAY_TYPE


def _format_date(value: str) -> str:
    """Format an ISO timestamp as 'Mon D, YYYY' (e.g. 'Oct 24, 2026')."""
    if not value:
        return ""
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return f"{dt.strftime('%b')} {dt.day}, {dt.year}"
    except (ValueError, TypeError):
        return value[:10]


templates.env.filters["format_date"] = _format_date
