from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict

PRODUCT_NAME = "RingGuard"  # the product name; change here only

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
    demo_responder_phones: str = ""  # "anita=+9198...,ravi=+9198..." used only by python -m app.seed
    dashboard_url: str = "http://localhost:5173"  # where invitation links and QR codes point (the laptop's address)
    invite_ttl_h: float = 48.0  # an invitation link works for this long
    email_mode: str = "auto"  # auto, live, simulate, off
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""  # secret: .env only
    smtp_from: str = ""  # e.g. "RingGuard <alerts@example.com>"
    wake_model: str = "hey_jarvis_v0.1"  # openWakeWord model name; replace with the custom model once trained
    wake_threshold: float = 0.5  # 0 to 1; higher is stricter (fewer false alarms, may miss some)
    wake_cooldown_s: float = 2.0  # after a trigger, ignore the mic this long
    wake_device: str = ""  # microphone number or name; empty = the default microphone
    wake_ack_sound: str = "media/ack/yes_boss.wav"  # played on trigger; relative to backend/
    data_key: str = ""  # secret: encrypts guard personal details and computes badge lookups. Keep it safe.
    badge_window_s: int = 300  # a badge scan this recent covers an entry into its area
    presence_window_s: int = 900  # who is 'present' at a time: a scan this recent in that area
    fire_station_number: str = ""  # only ever dialled when a human orders it and says "yes"
    site_address: str = ""  # read to the fire station on that call, e.g. "Plot 12, Hosur Road, Bengaluru"
    ppe_model_path: str = ""  # helmet/vest model (.pt); empty = PPE rule off until a model is chosen
    fire_model_path: str = ""  # fire/smoke model (.pt); empty = fire rule off until a model is chosen
    rumik_api_key: str = ""  # secret: Rumik Silk text to speech (the assistant's spoken replies)
    rumik_gateway_url: str = ""  # the gateway address shown in the Rumik dashboard (playground.rumik.ai)
    rumik_model: str = "mulberry"  # mulberry (takes a voice description) or muga
    rumik_speaker: str = ""  # a preset voice (mulberry); empty = Rumik's default
    rumik_description: str = "warm, clear Indian woman, calm and confident"  # mulberry only: Friday's voice in words
    rumik_timeout_s: float = 8.0
    friday_user: str = ""  # the wake-word listener acts as this user (their name), e.g. Anita Rao
    voice_silence_s: float = 1.0  # stop recording after this much quiet once you have started talking
    voice_max_s: float = 12.0  # never record longer than this
    voice_start_timeout_s: float = 5.0  # nothing said within this long after "Yes, boss": back to waiting
    voice_silence_rms: int = 500  # loudness (16-bit RMS) below this counts as quiet

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
    onscreen_grace_s: float = 5.0  # on the dashboard: this long to react before the phone rings
    stream_token_ttl_s: int = 3600
    api_base_url: str = "http://127.0.0.1:8000"  # where workers send /internal calls
    session_ttl_h: float = 12.0  # a login lasts one shift
    login_max_failures: int = 5  # wrong PINs allowed per client per window before 429
    login_window_s: float = 60.0
    worker_heartbeat_s: float = 5.0  # how often a worker reports online/fps to the API

    # cameras added from the dashboard (P22)
    workers_autostart: bool = False  # true: the API starts a worker for every enabled camera and restarts crashed ones
    worker_restart_s: float = 30.0  # wait this long before restarting a crashed worker
    worker_log_dir: str = "logs"  # one log file per camera worker, relative to backend/
    first_worker_port: int = 8101  # new cameras get the next free MJPEG port from here
    camera_probe_timeout_s: float = 8.0  # "test camera" gives up after this long
    staff_id_prefix: str = "RG"  # staff IDs look like RG-0007 (sent in the invitation email)

    # duty check-in (P23): only these roles check in; off duty = not called (shift roster is the fallback)
    duty_roles: str = "Head Guard,Guard"
    duty_max_h: float = 16.0  # a check-in older than this no longer counts (someone forgot to check out)
    camera_view_dedupe_s: float = 300.0  # one "camera viewed" log row per user and camera in this window

    # calls and messages: "auto" = live when the keys are set, otherwise off; "simulate" = log only, for demos
    calls_mode: str = "auto"  # auto, live, simulate, off
    whatsapp_mode: str = "auto"  # auto, live, simulate, off
    twilio_cps: float = 1.0  # Twilio places about 1 new call per second by default
    call_ring_timeout_s: int = 30
    call_language: str = "en-IN"
    role_admin: str = "Admin"  # role labels the call ladder looks for
    role_manager: str = "Manager"
    role_head_guard: str = "Head Guard"
    role_guard: str = "Guard"
    share_link_ttl_h: float = 24.0  # signed snapshot links sent on WhatsApp
    heartbeat_check_s: int = 10
    digest_lookback_h: int = 24
    broadcast_max_mb: float = 5.0
    voice_max_mb: float = 10.0

    # AI assistant
    llm_timeout_s: float = 5.0  # Groq
    ollama_timeout_s: float = 10.0
    ollama_default_model: str = "llama3.2:3b"
    whisper_offline_model: str = "small"
    assistant_confirm_ttl_s: int = 60
    assistant_max_rows: int = 50
    knowledge_dir: str = "knowledge"

    # PPE and fire models (labels differ between models, so they are configurable)
    ppe_helmet_labels: str = "helmet,hardhat,hard-hat"
    ppe_vest_labels: str = "vest,safety-vest,safety vest"
    fire_labels: str = "fire,smoke"
    ppe_missing_frames: int = 10
    fire_frames: int = 5

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
    number: int | None = None  # "camera 6" by voice; seed numbers the rest in order


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
