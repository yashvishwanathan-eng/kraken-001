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
    "false_alarm", "roll_call_responded", "roll_call_safe", "alert_all", "command", "duty_fallback",
    "call_skipped_on_screen", "nobody_answered",
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
Phone = Annotated[str, Field(pattern=r"^\+[1-9]\d{7,14}$", description="E.164, e.g. +919876543210")]
HhMm = Annotated[str, Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$", description="HH:MM, 24-hour")]
CallZone = Annotated[str, Field(min_length=1, max_length=120)]


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
    staff_id: str  # e.g. RG-0007, sent in the invitation email
    name: str
    role: str
    camera_ids: list[str]
    actions: list[str]
    active: bool
    phone: str | None  # on the call list; None only for people added before phones were required
    zone: str | None  # call zone ("call the guards of the entrance")
    shift_start: str | None
    shift_end: str | None


class UserCreate(BaseModel):
    name: Annotated[str, Field(min_length=1, max_length=120)]
    role: Annotated[str, Field(min_length=1, max_length=32)]
    camera_ids: list[str] | None = None  # None: the role's cameras (P22 roles), else none
    actions: list[str] | None = None  # None: the role's actions, else none
    active: bool = True
    pin: Pin
    phone: Phone  # every person is on the call list
    zone: CallZone = "general"
    shift_start: HhMm = "00:00"  # 00:00 to 00:00 = all day
    shift_end: HhMm = "00:00"


class UserUpdate(BaseModel):
    """Every field optional: send only what changes. Sending `pin` resets it."""
    name: Annotated[str, Field(min_length=1, max_length=120)] | None = None
    role: Annotated[str, Field(min_length=1, max_length=32)] | None = None
    camera_ids: list[str] | None = None
    actions: list[str] | None = None
    active: bool | None = None
    pin: Pin | None = None
    phone: Phone | None = None
    zone: CallZone | None = None
    shift_start: HhMm | None = None
    shift_end: HhMm | None = None


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
    number: int | None  # "camera 6" by voice
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
    detail: dict | None = None  # e.g. the question and answer of an assistant_query


# ---------- responders ----------

class ResponderIn(BaseModel):
    name: Annotated[str, Field(min_length=1, max_length=120)]
    phone: Phone
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


# ---------- alert everybody, roll call, broadcast ----------

class AlertAllIn(BaseModel):
    message: Annotated[str, Field(min_length=1, max_length=500)]
    incident_id: int | None = None


class AlertAllOut(BaseModel):
    kind: Literal["alert_all"]
    roll_call_id: int
    incident_id: int | None
    message: str
    by: str
    at: str
    recipients: int
    whatsapp_sent: int
    calls_queued: int


class RollCallGuard(BaseModel):
    id: int
    name: str
    status: Literal["pending", "responding", "safe", "not_reached"]


class RollCallOut(BaseModel):
    roll_call_id: int
    incident_id: int | None
    message: str
    total: int
    responding: int
    safe: int
    not_reached: int
    guards: list[RollCallGuard]


class BroadcastOut(BaseModel):
    zone: str
    recipients: list[str]
    queued: int
    calls_mode: str


# ---------- AI assistant ----------

AnswerSource = Literal["groq", "ollama", "keywords"]


class AssistantQueryIn(BaseModel):
    question: Annotated[str, Field(min_length=1, max_length=500)]


class AssistantAnswer(BaseModel):
    answer: str
    incidents: list[IncidentOut]
    source: AnswerSource
    ms: int


class AssistantVoiceAnswer(AssistantAnswer):
    transcript: str


class CommandIn(BaseModel):
    text: Annotated[str, Field(min_length=1, max_length=500)]


class CommandOut(BaseModel):
    intent: str
    status: Literal["done", "needs_confirmation", "unknown", "refused"]
    answer: str
    confirm_token: str | None
    ms: int
    data: dict


class ConfirmIn(BaseModel):
    confirm_token: Annotated[str, Field(min_length=1, max_length=100)]
    confirmed: bool


class ConfirmOut(BaseModel):
    status: Literal["done", "refused"]
    answer: str
    ms: int
    data: dict


# ---------- P19. Invitations ----------

class InviteCreate(BaseModel):
    name: Annotated[str, Field(min_length=1, max_length=120)]
    email: Annotated[str, Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$", max_length=254)]
    role: Annotated[str, Field(min_length=1, max_length=32)]
    camera_ids: list[str] | None = None  # None: the role's cameras
    actions: list[str] | None = None  # None: the role's actions
    phone: Phone  # on the call list as soon as they accept
    zone: CallZone = "general"
    shift_start: HhMm = "00:00"
    shift_end: HhMm = "00:00"


class InviteOut(BaseModel):
    invite_id: int
    user_id: int
    staff_id: str
    name: str
    email: str
    role: str
    status: Literal["pending", "accepted", "expired", "revoked"]
    expires_at: datetime
    created_by: str


class InviteCreated(InviteOut):
    link: str  # the link in the email and QR code; shown once, not stored
    qr_png: str  # data:image/png;base64,... for the dashboard to show
    email_status: Literal["sent", "simulated", "not_sent", "off"]


class InvitePreview(BaseModel):
    name: str
    email: str
    role: str
    expires_at: datetime


class InviteAccept(BaseModel):
    pin: Pin


# ---------- P20. Sector summaries ----------

class SummaryIncidents(BaseModel):
    total: int
    by_status: dict[str, int]
    by_type: dict[str, int]


class SummaryEntry(BaseModel):
    incident_id: int
    at: datetime
    camera_id: str
    zone: str | None
    status: str
    acknowledged_by: str | None
    badge_status: str  # scanned, not_scanned or unknown (camera not placed in an area yet)


class SummaryViolations(BaseModel):
    restricted_entries: int
    still_open: int
    without_badge: int
    items: list[SummaryEntry]


class SummaryCamera(BaseModel):
    camera_id: str
    number: int | None = None
    name: str
    online: bool
    fps: float
    incidents: int
    open: int


class SummaryLogin(BaseModel):
    at: datetime
    user_name: str
    channel: str


class SummarySector(BaseModel):
    all_cameras: bool
    camera_ids: list[str]


class SummaryOut(BaseModel):
    from_: datetime = Field(alias="from")
    to: datetime
    sector: SummarySector
    incidents: SummaryIncidents
    violations: SummaryViolations
    cameras: list[SummaryCamera]
    logins: list[SummaryLogin]  # empty unless the caller may view the audit log
    text: str  # plain English, built from the counts above; read aloud by the dashboard

    model_config = {"populate_by_name": True}


# ---------- P21. Sites, guards, badges ----------

class SiteCreate(BaseModel):
    name: Annotated[str, Field(min_length=1, max_length=120)]
    address: Annotated[str | None, Field(max_length=255)] = None


class BuildingCreate(BaseModel):
    name: Annotated[str, Field(min_length=1, max_length=120)]


class AreaCreate(BaseModel):
    name: Annotated[str, Field(min_length=1, max_length=120)]
    kind: Literal["room", "area"] = "room"


class CameraAreaIn(BaseModel):
    area_id: int | None = None


class AreaOut(BaseModel):
    id: int
    name: str
    kind: str
    camera_ids: list[str]


class BuildingOut(BaseModel):
    id: int
    name: str
    areas: list[AreaOut]


class SiteOut(BaseModel):
    id: int
    name: str
    address: str | None
    buildings: list[BuildingOut]


class SitesOut(BaseModel):
    sites: list[SiteOut]


class PersonalDetails(BaseModel):
    badge_number: Annotated[str, Field(max_length=80)] = ""
    badge_id: Annotated[str, Field(max_length=80)] = ""
    security_number: Annotated[str, Field(max_length=80)] = ""
    emergency_contact: Annotated[str, Field(max_length=200)] = ""


class GuardProfileIn(BaseModel):
    position: Annotated[str | None, Field(max_length=120)] = None
    site_id: int | None = None
    post: Annotated[str | None, Field(max_length=120)] = None
    equipment: Annotated[str | None, Field(max_length=500)] = None
    has_phone: bool = False
    personal: PersonalDetails | None = None  # replaces the stored details entirely; omit to clear them


class GuardListItem(BaseModel):
    user_id: int
    name: str
    role: str
    position: str | None
    post: str | None


class GuardProfileOut(BaseModel):
    user_id: int
    name: str
    role: str
    camera_ids: list[str]
    position: str | None
    site_id: int | None
    site_name: str | None
    post: str | None
    equipment: str | None
    has_phone: bool
    linked_login: bool
    has_personal_details: bool
    personal: PersonalDetails | None = None  # only on the admin's detail view


class BadgeScanIn(BaseModel):
    badge_number: Annotated[str, Field(min_length=1, max_length=80)]
    area_id: int | None = None
    reader: Annotated[str | None, Field(max_length=120)] = None


class BadgeScanOut(BaseModel):
    ok: bool
    guard_name: str
    at: datetime
    area_id: int | None


class PresentGuard(BaseModel):
    user_id: int
    name: str
    area_id: int | None
    area_name: str | None
    last_scan_at: datetime


class PresenceOut(BaseModel):
    at: datetime
    present: list[PresentGuard]


# ---------- P22. Cameras: add, test, start; roles ----------

CameraSource = Annotated[str, Field(min_length=1, max_length=255,
                                    description="0 (webcam), http://PHONE-IP:8080/video, rtsp://..., or a file path")]
CameraId = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9-]{1,63}$", description="lowercase letters, digits, -")]


class CameraTestIn(BaseModel):
    source: CameraSource


class CameraTestOut(BaseModel):
    ok: bool
    width: int | None
    height: int | None
    ms: int
    error: str | None


class CameraCreate(BaseModel):
    name: Annotated[str, Field(min_length=1, max_length=120)]
    source: CameraSource
    id: CameraId | None = None  # default: made from the name, e.g. cam-loading-bay
    number: Annotated[int, Field(ge=1, le=9999)] | None = None  # default: the next free number
    area_id: int | None = None
    role_names: list[str] = []  # give the camera to every user with these roles
    test_first: bool = True  # read one frame first; 422 if the camera does not answer
    start: bool = True  # start its worker straight away


class CameraUpdate(BaseModel):
    name: Annotated[str, Field(min_length=1, max_length=120)] | None = None
    source: CameraSource | None = None
    number: Annotated[int, Field(ge=1, le=9999)] | None = None
    enabled: bool | None = None


class WorkerStatus(BaseModel):
    managed: bool  # started by the API (False: started by hand, or not running)
    running: bool
    pid: int | None
    started_at: UtcDatetime | None
    exit_code: int | None


class CameraAdminOut(BaseModel):
    id: str
    number: int | None
    name: str
    source: str
    port: int
    enabled: bool
    area_id: int | None
    added_by: str | None  # None: from cameras.yaml
    online: bool
    fps: float
    worker: WorkerStatus


class WorkerLogOut(BaseModel):
    camera_id: str
    lines: list[str]


class RoleIn(BaseModel):
    name: Annotated[str, Field(min_length=1, max_length=32)]
    description: Annotated[str, Field(max_length=255)] | None = None
    camera_ids: list[str] = []
    actions: list[str] = []


class RoleUpdate(BaseModel):
    name: Annotated[str, Field(min_length=1, max_length=32)] | None = None
    description: Annotated[str, Field(max_length=255)] | None = None
    camera_ids: list[str] | None = None
    actions: list[str] | None = None


class RoleOut(BaseModel):
    id: int
    name: str
    description: str | None
    camera_ids: list[str]
    actions: list[str]
    users: int  # how many users have this role


# ---------- P23. Duty check-in ----------

DutyAction = Literal["in", "out", "toggle"]


class DutyPassOut(BaseModel):
    user_id: int
    staff_id: str
    name: str
    pass_token: str  # what the QR code holds
    qr_png: str  # data:image/png;base64,...


class DutyPostQrOut(BaseModel):
    area_id: int
    area_name: str
    link: str  # DASHBOARD_URL/check-in?post=...
    post_token: str
    qr_png: str


class DutyScanIn(BaseModel):
    pass_token: Annotated[str, Field(min_length=3, max_length=100)]
    action: DutyAction = "toggle"
    area_id: int | None = None


class DutyCheckIn(BaseModel):
    post_token: Annotated[str, Field(min_length=3, max_length=100)]
    action: DutyAction = "toggle"


class DutySetIn(BaseModel):
    user_id: int
    action: Literal["in", "out"]


class DutyEventOut(BaseModel):
    user_id: int
    name: str
    action: Literal["in", "out"]
    method: Literal["pass_scan", "post_qr", "dashboard", "voice"]
    at: UtcDatetime
    area_id: int | None
    by: str


class DutyPerson(BaseModel):
    user_id: int
    staff_id: str
    name: str
    role: str
    on_duty: bool
    since: UtcDatetime | None  # time of the latest check-in or check-out
    method: str | None
    area_id: int | None
    post: str | None
    equipment: str | None


class DutyBoardOut(BaseModel):
    on_duty: list[DutyPerson]
    off_duty: list[DutyPerson]


# ---------- P24. Overview, activity, data usage ----------

class OverviewCamera(BaseModel):
    id: str
    number: int | None
    name: str
    online: bool
    fps: float
    enabled: bool
    worker_running: bool


class OverviewProblem(BaseModel):
    kind: Literal["incident", "camera_offline", "no_guard_on_duty"]
    severity: Literal["critical", "high", "low"]
    text: str
    incident_id: int | None = None
    camera_id: str | None = None


class OverviewOut(BaseModel):
    at: UtcDatetime
    cameras_total: int
    cameras_online: int
    cameras_offline: int
    workers_running: int
    cameras: list[OverviewCamera]
    open_incidents: int
    open_by_severity: dict[str, int]
    problems_now: list[OverviewProblem]
    past_problems: list[DayCount]  # incidents per IST day, last 7 days
    past_total_7d: int
    guards_on_duty: list[str]
    guards_off_duty: list[str]
    ok: bool  # True when problems_now is empty
    text: str


class ActivityUser(BaseModel):
    user_id: int | None
    user: str
    role: str | None
    actions: int
    ai_questions: int
    ai_commands: int
    camera_views: int
    cameras_viewed: list[str]
    last_at: UtcDatetime | None


class ActivityAi(BaseModel):
    at: UtcDatetime
    user: str
    channel: Literal["dashboard", "phone", "voice"]
    kind: Literal["question", "command"]
    text: str
    answer: str
    intent: str | None
    status: str | None
    source: str | None


class ActivityView(BaseModel):
    at: UtcDatetime
    user: str
    camera_id: str


class ActivityOut(BaseModel):
    from_: datetime = Field(alias="from")
    to: datetime
    users: list[ActivityUser]
    ai: list[ActivityAi]
    camera_views: list[ActivityView]

    model_config = {"populate_by_name": True}


class UsageFiles(BaseModel):
    count: int
    bytes: int


class UsageOut(BaseModel):
    media_total_bytes: int
    snapshots: UsageFiles
    clips: UsageFiles
    other_media: UsageFiles
    worker_logs: UsageFiles
    disk_free_bytes: int
    disk_total_bytes: int
    database_bytes: int | None  # None if the database cannot say
    rows: dict[str, int]
    incidents_by_day: list[DayCount]  # last 30 IST days
    ai_queries_by_day: list[DayCount]


class SpeakIn(BaseModel):
    text: Annotated[str, Field(min_length=1, max_length=1000)]


# ---------- demo alarm ----------

class DemoIncidentIn(BaseModel):
    type: IncidentType = "fire"
    camera_id: str | None = None  # default: your first camera


class VoiceCommandOut(CommandOut):
    transcript: str  # what Friday heard


# ---------- recordings (P30): the all-day video of every camera ----------

class RecordingSegment(BaseModel):
    start: UtcDatetime
    end: UtcDatetime  # last write; the file still being recorded grows until its 5 minutes are up
    url: str  # /media/recordings/<camera_id>/<file>.mp4 (local, like clip_url)
    size_mb: float


class RecordingDaysOut(BaseModel):
    camera_id: str
    days: list[str]  # YYYY-MM-DD (IST), newest first


class RecordingDayOut(BaseModel):
    camera_id: str
    date: str
    segments: list[RecordingSegment]


class RecordingAtOut(BaseModel):
    camera_id: str
    url: str
    start: UtcDatetime
    end: UtcDatetime
    offset_s: float  # seconds into the file where that moment is
