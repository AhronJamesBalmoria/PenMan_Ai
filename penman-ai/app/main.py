from fastapi import FastAPI, Request, Response
from fastapi.responses import RedirectResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.routers import auth, pages, drafts, analysis
from app.exceptions import RedirectException

app = FastAPI(title="Penman AI", version="0.1.0")
templates = Jinja2Templates(directory="app/templates")


@app.exception_handler(RedirectException)
async def redirect_handler(request: Request, exc: RedirectException):
    if request.headers.get("HX-Request") == "true":
        return Response(status_code=200, headers={"HX-Redirect": exc.url})
    return RedirectResponse(url=exc.url, status_code=303)


@app.exception_handler(404)
async def not_found_handler(request: Request, exc):
    return templates.TemplateResponse(
        "404.html", {"request": request}, status_code=404
    )


@app.exception_handler(500)
async def server_error_handler(request: Request, exc):
    return templates.TemplateResponse(
        "500.html", {"request": request}, status_code=500
    )


app.mount("/static", StaticFiles(directory="app/static"), name="static")

app.include_router(pages.router)
app.include_router(auth.router)
app.include_router(drafts.router)
app.include_router(analysis.router)