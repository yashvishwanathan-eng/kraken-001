"""Recordings: the all-day video of every camera (worker/recorder.py writes it; this only reads the folder).

  GET /recordings/days?camera_id=cam-door                   days that have video
  GET /recordings?camera_id=cam-door&date=2026-10-26        that day's 5-minute files, oldest first
  GET /recordings/at?camera_id=cam-door&at=<ISO time>       the file that covers that moment + where in it

File names hold the start time in the laptop's local time (TIMEZONE, IST); the API answers in UTC like
every other time in the contract. Users only see recordings of their own delegated cameras.
"""

import re
from datetime import date as Date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query

from app.config import get_settings, media_root
from app.deps import current_user
from app.models import User
from app.schemas import RecordingAtOut, RecordingDayOut, RecordingDaysOut, RecordingSegment
from app.services.permissions import allowed_cameras
from worker.recorder import segment_start

router = APIRouter(prefix="/recordings", tags=["recordings"])

CAMERA_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
LATE_WRITE_S = 30  # a file's last write can be a little after its planned end


def _folder(camera_id: str, user: User) -> Path:
    if not CAMERA_ID.match(camera_id):
        raise HTTPException(status_code=422, detail="camera_id may only contain letters, digits, - and _")
    scope = allowed_cameras(user)
    if scope is not None and camera_id not in scope:
        raise HTTPException(status_code=403, detail="this camera is not delegated to you")
    return media_root() / "recordings" / camera_id


def segments(folder: Path, day: Date | None = None) -> list[RecordingSegment]:
    tz = ZoneInfo(get_settings().timezone)
    length = timedelta(seconds=get_settings().record_segment_s + LATE_WRITE_S)
    out = []
    for f in sorted(folder.glob("*.mp4")):
        start_local = segment_start(f)
        if start_local is None or (day is not None and start_local.date() != day):
            continue
        start = start_local.replace(tzinfo=tz).astimezone(timezone.utc)
        stat = f.stat()
        end = min(datetime.fromtimestamp(stat.st_mtime, timezone.utc), start + length)
        out.append(RecordingSegment(start=start, end=max(end, start), size_mb=round(stat.st_size / 1024 ** 2, 1),
                                    url=f"/media/recordings/{folder.name}/{f.name}"))
    return out


@router.get("/days", response_model=RecordingDaysOut)
def days(camera_id: str, user: User = Depends(current_user)) -> RecordingDaysOut:
    folder = _folder(camera_id, user)
    found = sorted({s.date().isoformat() for f in folder.glob("*.mp4") if (s := segment_start(f))}, reverse=True)
    return RecordingDaysOut(camera_id=camera_id, days=found)


@router.get("", response_model=RecordingDayOut)
def day(camera_id: str, date: Date | None = Query(None, description="YYYY-MM-DD in IST; default today"),
        user: User = Depends(current_user)) -> RecordingDayOut:
    folder = _folder(camera_id, user)
    wanted = date or datetime.now(ZoneInfo(get_settings().timezone)).date()
    return RecordingDayOut(camera_id=camera_id, date=wanted.isoformat(), segments=segments(folder, wanted))


@router.get("/at", response_model=RecordingAtOut)
def at(camera_id: str, at: datetime, user: User = Depends(current_user)) -> RecordingAtOut:
    """The recording that covers this moment (e.g. an incident), and how many seconds into it to start."""
    folder = _folder(camera_id, user)
    moment = at if at.tzinfo else at.replace(tzinfo=timezone.utc)
    for seg in reversed(segments(folder)):
        if seg.start <= moment <= seg.end + timedelta(seconds=LATE_WRITE_S):
            return RecordingAtOut(camera_id=camera_id, url=seg.url, start=seg.start, end=seg.end,
                                  offset_s=max(0.0, (moment - seg.start).total_seconds()))
    raise HTTPException(status_code=404, detail="no recording covers that time (the camera was off or it was deleted)")
