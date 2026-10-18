from typing import Literal

from pydantic import BaseModel


class CameraHealth(BaseModel):
    id: str
    online: bool
    fps: float


class HealthResponse(BaseModel):
    cameras: list[CameraHealth]
    db: Literal["ok", "error", "not_checked"]
    twilio: Literal["ok", "not_configured", "error", "not_checked"]
    llm: Literal["ok", "ollama_only", "error", "not_checked"]
