"""Single shared Jinja2 template environment."""
from fastapi.templating import Jinja2Templates

from app.config import BASE_DIR
from app.constants import DEFAULT_ESSAY_TYPE, ESSAY_TYPES

templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
templates.env.globals["ESSAY_TYPES"] = ESSAY_TYPES
templates.env.globals["DEFAULT_ESSAY_TYPE"] = DEFAULT_ESSAY_TYPE
