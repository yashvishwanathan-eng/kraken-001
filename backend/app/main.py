from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.config import PRODUCT_NAME, get_settings, media_root
from app.routers import health, internal

settings = get_settings()

app = FastAPI(title=PRODUCT_NAME)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/media", StaticFiles(directory=media_root()), name="media")

app.include_router(health.router)
app.include_router(internal.router)
