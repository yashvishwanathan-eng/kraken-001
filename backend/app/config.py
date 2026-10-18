from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

PRODUCT_NAME = "SafetyAI"  # placeholder name; change here only


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

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
    cors_origins: list[str] = ["http://localhost:5173"]


@lru_cache
def get_settings() -> Settings:
    return Settings()
