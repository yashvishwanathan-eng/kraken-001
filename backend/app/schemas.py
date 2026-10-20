"""Pydantic request/response models. These mirror docs/CONTRACT.md exactly."""

from datetime import datetime, timezone
from pathlib import PurePath
from typing import Annotated, Literal

from pydantic import BaseModel, Field, PlainSerializer, field_serializer

from app import models


def iso_utc(dt: datetime) -> str:
    """UTC, whole seconds, with a Z: 2026-10-24T10:12:41Z (the contract format)."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


UtcDatetime = Annotated[datetime, PlainSerializer(iso_utc, return_type=str)]

IncidentType = Literal["access", "ppe", "fall", "fire", "unattended", "loitering", "crowding"]
Severity = Literal["critical", "high", "low"]
Status = Literal["open", "acknowledged", "resolved", "false_alarm"]
TimelineAction = Literal[
    "detected", "call_placed", "call_no_answer", "acknowledged", "escalated", "whatsapp_sent", "resolved",
    "false_alarm", "roll_call_responded", "roll_call_safe", "alert_all", "command",
]
Via = Literal["keypad", "voice", "dashboard"]


class CameraHealth(BaseModel):
    id: str
    online: bool
    fps: float


class HealthResponse(BaseModel):
    cameras: list[CameraHealth]
    db: Literal["ok", "error", "not_checked"]
    twilio: Literal["ok", "not_configured", "error", "not_checked"]
    llm: Literal["ok", "ollama_only", "error", "not_checked"]


class TimelineItem(BaseModel):
    at: UtcDatetime
    action: TimelineAction
    to: str | None = None
    by: str | None = None
    via: Via | None = None


def media_url(path: str | None) -> str | None:
    return f"/media/{PurePath(path).name}" if path else None


class IncidentOut(BaseModel):
    id: int
    type: IncidentType
    severity: Severity
    status: Status
    camera_id: str
    camera_name: str
    zone: str | None
    started_at: UtcDatetime
    confidence: float
    snapshot_url: str | None
    clip_url: str | None
    acknowledged_by: str | None
    timeline: list[TimelineItem]

    @field_serializer("timeline")
    def _timeline_without_empty_keys(self, items: list[TimelineItem]) -> list[dict]:
        # The contract shows only the keys each action uses (e.g. "to" on call_placed, "by"/"via" on acknowledged).
        return [item.model_dump(mode="json", exclude_none=True) for item in items]

    @classmethod
    def from_model(cls, incident: models.Incident) -> "IncidentOut":
        return cls(
            id=incident.id, type=incident.type, severity=incident.severity, status=incident.status,
            camera_id=incident.camera_id, camera_name=incident.camera.name, zone=incident.zone,
            started_at=incident.started_at, confidence=round(incident.confidence, 2),
            snapshot_url=media_url(incident.snapshot_path), clip_url=media_url(incident.clip_path),
            acknowledged_by=incident.acknowledged_by,
            timeline=[
                TimelineItem(at=e.at, action=e.action, to=e.target, by=e.actor, via=e.via)
                for e in incident.timeline
            ],
        )


class OkResponse(BaseModel):
    ok: bool


# ---------- auth and users ----------

Pin = Annotated[str, Field(pattern=r"^\d{4,8}$", description="4 to 8 digits")]


class PinLogin(BaseModel):
    pin: Pin


class UserBrief(BaseModel):
    id: int
    name: str
    role: str


class LoginResponse(BaseModel):
    token: str
    user: UserBrief


class MeResponse(BaseModel):
    id: int
    name: str
    role: str
    camera_ids: list[str]
    actions: list[str]


class UserOut(BaseModel):
    id: int
    name: str
    role: str
    camera_ids: list[str]
    actions: list[str]
    active: bool


class UserCreate(BaseModel):
    name: Annotated[str, Field(min_length=1, max_length=120)]
    role: Annotated[str, Field(min_length=1, max_length=32)]
    camera_ids: list[str] = []
    actions: list[str] = []
    active: bool = True
    pin: Pin


class UserUpdate(BaseModel):
    """Every field optional: send only what changes. Sending `pin` resets it."""
    name: Annotated[str, Field(min_length=1, max_length=120)] | None = None
    role: Annotated[str, Field(min_length=1, max_length=32)] | None = None
    camera_ids: list[str] | None = None
    actions: list[str] | None = None
    active: bool | None = None
    pin: Pin | None = None


# ---------- cameras and zones ----------

ZoneKind = Literal["restricted", "ppe", "work"]
Fraction = Annotated[float, Field(ge=0.0, le=1.0)]


class ZoneOut(BaseModel):
    id: int
    camera_id: str
    name: str
    kind: ZoneKind
    polygon: list[tuple[float, float]]
    max_people: int | None


class ZoneIn(BaseModel):
    camera_id: Annotated[str, Field(min_length=1, max_length=64)]
    name: Annotated[str, Field(min_length=1, max_length=120)]
    kind: ZoneKind
    polygon: Annotated[list[tuple[Fraction, Fraction]], Field(min_length=3, max_length=50)]
    max_people: Annotated[int, Field(ge=1)] | None = None


class CameraOut(BaseModel):
    id: str
    name: str
    stream_url: str
    online: bool
    fps: float
    zones: list[ZoneOut]


class StreamUrlResponse(BaseModel):
    stream_url: str


# ---------- events and stats ----------

class ResolveIn(BaseModel):
    outcome: Literal["resolved", "false_alarm"]
    note: Annotated[str, Field(max_length=500)] | None = None


class DayCount(BaseModel):
    date: str  # YYYY-MM-DD in IST
    count: int


class StatsOut(BaseModel):
    by_type: dict[str, int]
    by_day: list[DayCount]
    open: int
    resolved: int
    avg_response_s: float | None


class AuditRow(BaseModel):
    at: UtcDatetime
    user: str
    action: str
    target: str | None
    channel: Literal["dashboard", "phone", "voice"]


# ---------- responders ----------

HhMm = Annotated[str, Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$", description="HH:MM, 24-hour")]


class ResponderIn(BaseModel):
    name: Annotated[str, Field(min_length=1, max_length=120)]
    phone: Annotated[str, Field(pattern=r"^\+[1-9]\d{7,14}$", description="E.164, e.g. +919876543210")]
    language: Literal["en", "ta", "hi"] = "en"
    zone: Annotated[str, Field(min_length=1, max_length=120)]
    shift_start: HhMm
    shift_end: HhMm
    call_order: Annotated[int, Field(ge=1, le=99)] = 1
    active: bool = True


class ResponderOut(ResponderIn):
    id: int


# ---------- worker heartbeat ----------

class HeartbeatIn(BaseModel):
    camera_id: Annotated[str, Field(min_length=1, max_length=64)]
    fps: Annotated[float, Field(ge=0)]
    online: bool
    ts: datetime
