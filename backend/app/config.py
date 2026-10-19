from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict

PRODUCT_NAME = "SafetyAI"  # placeholder name; change here only

BACKEND_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BACKEND_DIR / ".env", extra="ignore")

    # .env keys (secrets, machine-specific)
    database_url: str = ""
    internal_key: str = ""
    public_base_url: str = ""
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_from_number: str = ""
    whatsapp_api_url: str = ""
    whatsapp_token: str = ""
    groq_api_key: str = ""
    groq_model: str = ""
    groq_stt_model: str = ""
    ollama_url: str = ""
    ollama_model: str = ""
    offline_mode: bool = False
    media_dir: str = "media"

    # config values (not secrets; defaults match CLAUDE.md)
    cors_origins: list[str] = ["http://localhost:5173"]
    camera_config_path: str = "config/cameras.yaml"
    yolo_model_path: str = "models/yolo11n-pose.pt"
    detect_every_n_frames: int = 3
    detect_imgsz: int = 480
    detect_conf: float = 0.5
    night_start: str = "20:00"
    night_end: str = "06:00"
    timezone: str = "Asia/Kolkata"
    digest_time: str = "18:00"
    heartbeat_timeout_s: int = 30
    low_repeat_count: int = 3
    low_repeat_window_min: int = 60
    critical_admin_delay_s: int = 60
    stream_token_ttl_s: int = 3600


class CameraConfig(BaseModel):
    id: str
    name: str
    source: str
    port: int


@lru_cache
def get_settings() -> Settings:
    return Settings()


def load_cameras(path: str | None = None) -> list[CameraConfig]:
    """Read config/cameras.yaml, relative to backend/ unless absolute."""
    config_file = Path(path or get_settings().camera_config_path)
    if not config_file.is_absolute():
        config_file = BACKEND_DIR / config_file
    raw = yaml.safe_load(config_file.read_text(encoding="utf-8")) or {}
    return [CameraConfig(**item) for item in raw.get("cameras", [])]
