from fastapi import FastAPI, Request, Response
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.config import BASE_DIR, settings
from app.exceptions import RedirectException
from app.middleware import SecurityHeadersMiddleware, SessionMiddleware
from app.routers import analysis, auth, drafts, pages
from app.templating import templates

settings.validate()

app = FastAPI(title="Penman AI", version="0.2.0")


@app.exception_handler(RedirectException)
async def redirect_handler(request: Request, exc: RedirectException):
    if request.headers.get("HX-Request") == "true":
        return Response(status_code=200, headers={"HX-Redirect": exc.url})
    return RedirectResponse(url=exc.url, status_code=303)


@app.exception_handler(404)
async def not_found_handler(request: Request, exc):
    return templates.TemplateResponse(
        "404.html",
        {"request": request, "user": getattr(request.state, "user", None)},
        status_code=404,
    )


@app.exception_handler(500)
async def server_error_handler(request: Request, exc):
    return templates.TemplateResponse(
        "500.html",
        {"request": request, "user": getattr(request.state, "user", None)},
        status_code=500,
    )


app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

app.add_middleware(SessionMiddleware)
app.add_middleware(SecurityHeadersMiddleware, hsts=settings.COOKIE_SECURE)
app.add_middleware(GZipMiddleware, minimum_size=500)

app.include_router(pages.router)
app.include_router(auth.router)
app.include_router(drafts.router)
app.include_router(analysis.router)
