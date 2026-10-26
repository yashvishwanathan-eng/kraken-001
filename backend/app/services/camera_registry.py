"""P22. Which cameras exist: the database first, config/cameras.yaml as the fallback.

Cameras come from two places. `python -m app.seed` copies config/cameras.yaml into the database, and an admin
can add more from the dashboard (POST /cameras). Workers, /health and the heartbeat watchdog read the
database, so a camera added on the dashboard works without editing any file. If the database cannot be
reached, the yaml file is used, so a worker can still start.

Also here: the "test camera" probe (open the address, read one frame) and the next free port and number.
"""

import logging
import re
import threading
import time
from dataclasses import dataclass

import cv2
from sqlalchemy import func, select
from sqlalchemy.orm import Session as DbSession

from app.config import CameraConfig, get_settings, load_cameras
from app.db import SessionLocal
from app.models import Camera
from worker.capture import parse_source

log = logging.getLogger("safetyai.cameras")


def _config(row: Camera) -> CameraConfig:
    return CameraConfig(id=row.id, name=row.name, source=row.source, port=row.port, number=row.number)


def all_cameras(enabled_only: bool = False) -> list[CameraConfig]:
    """Every camera, by number then id. Falls back to cameras.yaml when the database is down."""
    try:
        with SessionLocal() as db:
            query = select(Camera).order_by(Camera.number.nulls_last(), Camera.id)
            if enabled_only:
                query = query.where(Camera.enabled.is_(True))
            rows = list(db.scalars(query))
        if rows:
            return [_config(r) for r in rows]
    except Exception:
        log.warning("cameras: database unreachable, using cameras.yaml", exc_info=True)
    return load_cameras()


def get_camera(camera_id: str) -> CameraConfig | None:
    return next((c for c in all_cameras() if c.id == camera_id), None)


def names() -> dict[str, str]:
    return {c.id: c.name for c in all_cameras()}


def slug(name: str) -> str:
    """'Loading Bay 2' -> 'cam-loading-bay-2'"""
    text = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:50] or "camera"
    return text if text.startswith("cam-") else f"cam-{text}"


def next_port(db: DbSession) -> int:
    highest = db.scalar(select(func.max(Camera.port)))
    return max(get_settings().first_worker_port, (highest or 0) + 1)


def next_number(db: DbSession) -> int:
    return (db.scalar(select(func.max(Camera.number))) or 0) + 1


@dataclass
class ProbeResult:
    ok: bool
    width: int | None = None
    height: int | None = None
    ms: int = 0
    error: str | None = None


def probe(source: str, timeout_s: float | None = None) -> ProbeResult:
    """Open the camera address and read one frame, giving up after the timeout.

    OpenCV can hang for a long time on an address that does not answer, so the read runs on its own
    thread and we stop waiting for it after the timeout (the thread finishes on its own later).
    """
    timeout_s = timeout_s or get_settings().camera_probe_timeout_s
    result: dict = {}
    t0 = time.perf_counter()

    def read() -> None:
        cap = cv2.VideoCapture(parse_source(source))
        try:
            ok, frame = cap.read() if cap.isOpened() else (False, None)
            result["frame"] = frame if ok else None
            result["opened"] = cap.isOpened()
        finally:
            cap.release()

    thread = threading.Thread(target=read, daemon=True, name="camera-probe")
    thread.start()
    thread.join(timeout_s)
    ms = int((time.perf_counter() - t0) * 1000)
    if thread.is_alive():
        return ProbeResult(False, ms=ms, error=f"no answer within {timeout_s:.0f} s: check the address and that "
                                               "the camera is on the same Wi-Fi")
    frame = result.get("frame")
    if frame is None:
        reason = "opened, but no picture came back" if result.get("opened") else "could not open this address"
        return ProbeResult(False, ms=ms, error=reason)
    height, width = frame.shape[:2]
    return ProbeResult(True, width=width, height=height, ms=ms)
