import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.config import BASE_DIR, settings
from app.exceptions import RedirectException
from app.middleware import SecurityHeadersMiddleware, SessionMiddleware
from app.routers import analysis, auth, drafts, pages
from app.services.style_profiler import warm_up_embedder
from app.templating import templates

settings.validate()

# Strong reference so the warm-up task is not garbage-collected mid-flight.
_warmup_task: asyncio.Task | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Warm the ONNX embedder in the background.

    Loading happens in a worker thread, so /health responds immediately and a
    failed model download (offline dev, blocked CDN) never blocks boot: the
    first analysis simply retries the load lazily.
    """
    global _warmup_task
    if settings.APP_ENV.strip().lower() != "test":
        _warmup_task = asyncio.create_task(asyncio.to_thread(warm_up_embedder))
    try:
        yield
    finally:
        if _warmup_task is not None:
            _warmup_task.cancel()


app = FastAPI(title="Penman AI", version="0.2.0", lifespan=lifespan)


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
