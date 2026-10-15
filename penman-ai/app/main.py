from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.routers import auth, pages

app = FastAPI(title="Penman AI", version="0.1.0")

app.mount("/static", StaticFiles(directory="app/static"), name="static")

app.include_router(pages.router)
app.include_router(auth.router)