from fastapi import FastAPI, Request, Response
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.routers import auth, pages, drafts, analysis
from app.exceptions import RedirectException

app = FastAPI(title="Penman AI", version="0.1.0")


@app.exception_handler(RedirectException)
async def redirect_handler(request: Request, exc: RedirectException):
    # If the request came from HTMX, tell HTMX to do a full-page redirect
    if request.headers.get("HX-Request") == "true":
        return Response(status_code=200, headers={"HX-Redirect": exc.url})
    # Otherwise return a normal redirect
    return RedirectResponse(url=exc.url, status_code=303)


app.mount("/static", StaticFiles(directory="app/static"), name="static")

app.include_router(pages.router)
app.include_router(auth.router)
app.include_router(drafts.router)
app.include_router(analysis.router)