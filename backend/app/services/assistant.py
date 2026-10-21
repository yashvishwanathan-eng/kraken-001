"""Questions about incidents: "what happened at the loading bay last night?"

1. The question becomes a FILTER, never SQL:
     {types[], cameras[], severities[], statuses[], from, to, limit}
   by Groq/Ollama (JSON mode) if available, otherwise by a keyword parser. Pydantic checks every value;
   anything invalid from the model falls back to the keyword parser.
2. Our code builds the database query from the filter, limited to the user's cameras.
3. The answer text is written from the rows found (count + short summary), so it cannot invent incidents.
"""

import logging
import re
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field, ValidationError, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession, selectinload

from app.config import get_settings
from app.models import Camera, Incident, User
from app.schemas import IncidentType, Severity, Status
from app.services.escalation import TYPE_LABELS
from app.services.llm import LlmUnavailable, chat_json
from app.services.permissions import allowed_cameras

log = logging.getLogger("safetyai.assistant")

NOT_FOUND = "I couldn't find that. Try asking about an incident type, a camera, or a time, like 'falls today'."

TYPE_WORDS = {
    "fire": ("fire", "smoke", "flame", "burning"), "fall": ("fall", "fell", "fallen", "down", "collapsed"),
    "access": ("access", "restricted", "entry", "entered", "intrusion", "trespass", "unauthorised", "unauthorized"),
    "ppe": ("ppe", "helmet", "hardhat", "vest"), "unattended": ("bag", "unattended", "backpack", "suitcase"),
    "loitering": ("loiter", "loitering", "hanging"), "crowding": ("crowd", "crowding", "crowded", "overcrowding"),
}
SEVERITY_WORDS = {"critical": ("critical", "serious", "emergency"), "high": ("high",), "low": ("low", "minor")}
STATUS_WORDS = {"open": ("open", "unresolved", "pending", "active"), "acknowledged": ("acknowledged",),
                "resolved": ("resolved", "closed"), "false_alarm": ("false alarm", "false alarms")}


class IncidentFilter(BaseModel):
    types: list[IncidentType] = []
    cameras: list[str] = []
    severities: list[Severity] = []
    statuses: list[Status] = []
    from_: datetime | None = Field(None, alias="from")
    to: datetime | None = None
    limit: int = Field(20, ge=1, le=50)

    model_config = {"populate_by_name": True, "extra": "ignore"}

    @field_validator("from_", "to")
    @classmethod
    def _utc(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value

    def is_empty(self) -> bool:
        return not (self.types or self.cameras or self.severities or self.statuses or self.from_ or self.to)


# ---------- question -> filter ----------

def _has(text: str, phrase: str) -> bool:
    """Whole-word match that also accepts a plural: "fall" matches "falls", "crowd" matches "crowds"."""
    return re.search(rf"\b{re.escape(phrase)}(?:s|es)?\b", text) is not None


def keyword_filter(question: str, cameras: list[Camera], now_utc: datetime, tz: str) -> IncidentFilter:
    """No model needed: look for known words. Covers the common questions well enough for a demo."""
    q = question.lower()
    local_now = now_utc.astimezone(ZoneInfo(tz))
    midnight = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    start: datetime | None = None
    end: datetime | None = None
    if m := re.search(r"\b(?:last|past)\s+(\d+)\s*(minute|min|hour|hr|day)s?\b", q):
        unit = {"minute": "minutes", "min": "minutes", "hour": "hours", "hr": "hours", "day": "days"}[m.group(2)]
        start = local_now - timedelta(**{unit: int(m.group(1))})
    elif _has(q, "last hour") or _has(q, "past hour"):
        start = local_now - timedelta(hours=1)
    elif _has(q, "last night"):
        start = midnight - timedelta(hours=4)  # yesterday 20:00
        end = start + timedelta(hours=10)  # to today 06:00
    elif _has(q, "tonight"):
        # asked in the afternoon or evening: today 20:00; asked before noon: the night already running
        start = midnight + timedelta(hours=20) if local_now.hour >= 12 else midnight - timedelta(hours=4)
        end = start + timedelta(hours=10)
    elif _has(q, "yesterday"):
        start, end = midnight - timedelta(days=1), midnight
    elif _has(q, "today"):
        start = midnight
    elif _has(q, "this week") or _has(q, "last week") or _has(q, "past week"):
        start = midnight - timedelta(days=7)

    camera_ids = []
    for camera in cameras:
        name_words = [w for w in re.findall(r"[a-z0-9]+", camera.name.lower()) if len(w) > 3]
        if camera.id.lower() in q or any(_has(q, w) for w in name_words):
            camera_ids.append(camera.id)

    return IncidentFilter(
        types=[t for t, ws in TYPE_WORDS.items() if any(_has(q, w) for w in ws)],
        cameras=camera_ids,
        severities=[s for s, ws in SEVERITY_WORDS.items() if any(_has(q, w) for w in ws)],
        statuses=[s for s, ws in STATUS_WORDS.items() if any(_has(q, w) for w in ws)],
        from_=start.astimezone(timezone.utc) if start else None,
        to=end.astimezone(timezone.utc) if end else None,
    )


def llm_filter(question: str, cameras: list[Camera], now_utc: datetime, tz: str) -> tuple[IncidentFilter, str]:
    local_now = now_utc.astimezone(ZoneInfo(tz))
    camera_list = ", ".join(f"{c.id} ({c.name})" for c in cameras) or "none"
    system = (
        "Turn a question about safety incidents into a JSON filter. Reply with JSON only, keys: "
        '"types" (any of access, ppe, fall, fire, unattended, loitering, crowding), "cameras" (camera ids), '
        '"severities" (critical, high, low), "statuses" (open, acknowledged, resolved, false_alarm), '
        '"from" and "to" (ISO 8601 with timezone, or null), "limit" (1-50). Use empty lists when not mentioned. '
        f"Now it is {local_now.isoformat()} ({tz}). Night means 20:00 to 06:00. Cameras: {camera_list}."
    )
    value, source = chat_json([{"role": "system", "content": system}, {"role": "user", "content": question}])
    return IncidentFilter.model_validate(value), source


def parse_question(question: str, cameras: list[Camera], now_utc: datetime | None = None) -> tuple[IncidentFilter, str]:
    """Returns (filter, source) with source groq, ollama or keywords."""
    now_utc = now_utc or datetime.now(timezone.utc)
    tz = get_settings().timezone
    try:
        return llm_filter(question, cameras, now_utc, tz)
    except (LlmUnavailable, ValidationError) as exc:
        log.info("question parsed by keywords (%s)", type(exc).__name__)
        return keyword_filter(question, cameras, now_utc, tz), "keywords"


# ---------- filter -> rows -> answer ----------

def run_filter(db: DbSession, f: IncidentFilter, user: User) -> list[Incident]:
    query = select(Incident).options(selectinload(Incident.timeline), selectinload(Incident.camera))
    scope = allowed_cameras(user)
    if scope is not None:
        query = query.where(Incident.camera_id.in_(scope))
    if f.types:
        query = query.where(Incident.type.in_(f.types))
    if f.cameras:
        query = query.where(Incident.camera_id.in_(f.cameras))
    if f.severities:
        query = query.where(Incident.severity.in_(f.severities))
    if f.statuses:
        query = query.where(Incident.status.in_(f.statuses))
    if f.from_:
        query = query.where(Incident.started_at >= f.from_)
    if f.to:
        query = query.where(Incident.started_at <= f.to)
    limit = min(f.limit, get_settings().assistant_max_rows)
    return list(db.scalars(query.order_by(Incident.started_at.desc(), Incident.id.desc()).limit(limit)))


def answer_text(rows: list[Incident], tz: str) -> str:
    if not rows:
        return "No incidents match that."
    counts: dict[str, int] = {}
    for incident in rows:
        counts[incident.type] = counts.get(incident.type, 0) + 1
    parts = [f"{n} {TYPE_LABELS.get(t, t).lower()}" for t, n in sorted(counts.items(), key=lambda kv: -kv[1])]
    latest = rows[0]
    when = latest.started_at.astimezone(ZoneInfo(tz)).strftime("%I:%M %p on %d %b").lstrip("0")
    where = latest.camera.name + (f", {latest.zone.replace('-', ' ')}" if latest.zone else "")
    total = len(rows)
    return (f"Found {total} incident{'s' if total != 1 else ''}: {', '.join(parts)}. "
            f"Latest: {TYPE_LABELS.get(latest.type, latest.type).lower()} at {where}, {when} ({latest.status.replace('_', ' ')}).")


def ask(db: DbSession, question: str, user: User) -> tuple[str, list[Incident], str, int]:
    """Returns (answer, incidents, source, ms)."""
    t0 = time.perf_counter()
    cameras = list(db.scalars(select(Camera).order_by(Camera.id)))
    scope = allowed_cameras(user)
    if scope is not None:
        cameras = [c for c in cameras if c.id in scope]
    f, source = parse_question(question, cameras)
    if f.is_empty() and source == "keywords":
        ms = int((time.perf_counter() - t0) * 1000)
        log.info("step=assistant_query source=%s rows=0 ms=%d (no filter found)", source, ms)
        return NOT_FOUND, [], source, ms
    rows = run_filter(db, f, user)
    ms = int((time.perf_counter() - t0) * 1000)
    log.info("step=assistant_query source=%s rows=%d ms=%d", source, len(rows), ms)
    return answer_text(rows, get_settings().timezone), rows, source, ms
