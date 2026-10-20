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
    stream_secret: str = ""  # signs stream URLs; if empty, a random one is made at startup (URLs die on restart)
    demo_user_pins: str = ""  # "anita=1111,vikram=2222,..." used only by python -m app.seed

    # config values (not secrets; defaults match CLAUDE.md)
    cors_origins: list[str] = ["http://localhost:5173"]
    camera_config_path: str = "config/cameras.yaml"
    yolo_model_path: str = "models/yolo11n-pose.pt"
    bag_model_path: str = "models/yolo11n.pt"  # plain detector, used only for backpack/handbag/suitcase
    prefer_openvino: bool = True  # use models/<name>_openvino_model/ when it exists (about 2x faster on Intel CPUs)
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
    api_base_url: str = "http://127.0.0.1:8000"  # where workers send /internal calls
    session_ttl_h: float = 12.0  # a login lasts one shift
    login_max_failures: int = 5  # wrong PINs allowed per client per window before 429
    login_window_s: float = 60.0
    worker_heartbeat_s: float = 5.0  # how often a worker reports online/fps to the API

    # incident rule thresholds (CLAUDE.md "Incident rules")
    incident_cooldown_s: float = 60.0
    access_dwell_s: float = 3.0
    loiter_s: float = 120.0
    fall_angle_deg: float = 60.0  # torso this far from vertical counts as "near-horizontal"
    upright_angle_deg: float = 30.0  # below this the person is upright; used to learn hip height
    fall_hip_drop: float = 0.25  # hips must drop by this fraction of the upright body height
    fall_down_s: float = 5.0
    unattended_s: float = 60.0
    bag_still_frac: float = 0.03  # bag centre may move this fraction of frame width and still be "still"
    bag_reach_frac: float = 0.15  # a person within this fraction of frame width is "within reach"
    bag_detect_interval_s: float = 1.0  # bags barely move, so check them about once a second
    track_grace_s: float = 1.0  # keep a timer alive this long if a track briefly disappears
    clip_before_s: float = 5.0
    clip_after_s: float = 5.0
    ring_buffer_s: float = 12.0
    ring_max_width: int = 640  # frames are shrunk to this width before buffering


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


def media_root() -> Path:
    """Absolute folder for clips and snapshots, shared by the API (serves /media) and the workers (write)."""
    path = Path(get_settings().media_dir)
    if not path.is_absolute():
        path = BACKEND_DIR / path
    path.mkdir(parents=True, exist_ok=True)
    return path
