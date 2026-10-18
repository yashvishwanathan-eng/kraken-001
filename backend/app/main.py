from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.config import BACKEND_DIR, PRODUCT_NAME, get_settings
from app.routers import health

settings = get_settings()

app = FastAPI(title=PRODUCT_NAME)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

media_dir = Path(settings.media_dir)
if not media_dir.is_absolute():
    media_dir = BACKEND_DIR / media_dir
media_dir.mkdir(parents=True, exist_ok=True)
app.mount("/media", StaticFiles(directory=media_dir), name="media")

app.include_router(health.router)
