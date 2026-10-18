from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.config import PRODUCT_NAME, get_settings
from app.schemas import HealthResponse

settings = get_settings()

app = FastAPI(title=PRODUCT_NAME)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

media_dir = Path(settings.media_dir)
media_dir.mkdir(parents=True, exist_ok=True)
app.mount("/media", StaticFiles(directory=media_dir), name="media")


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    # Stub: cameras, DB, Twilio and LLM checks are added in later tasks.
    return HealthResponse(cameras=[], db="not_checked", twilio="not_checked", llm="not_checked")
