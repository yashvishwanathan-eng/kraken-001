"""Pydantic request/response models. These mirror docs/CONTRACT.md exactly."""

from datetime import datetime, timezone
from pathlib import PurePath
from typing import Annotated, Literal

from pydantic import BaseModel, PlainSerializer, field_serializer

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
