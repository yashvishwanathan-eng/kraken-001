"""Live camera status from worker heartbeats (kept in memory; it is only ever "right now").

A worker posts {camera_id, fps, online} every few seconds. A camera counts as online only if its
worker said so recently: a worker that crashed stops sending, and goes offline after the timeout.
"""

import threading
import time

from app.config import get_settings

_lock = threading.Lock()
_status: dict[str, tuple[bool, float, float]] = {}  # camera_id -> (online, fps, monotonic time received)


def update(camera_id: str, online: bool, fps: float) -> None:
    with _lock:
        _status[camera_id] = (online, fps, time.monotonic())


def get(camera_id: str) -> tuple[bool, float]:
    """(online, fps). Offline with 0 fps if no heartbeat within HEARTBEAT_TIMEOUT_S."""
    with _lock:
        entry = _status.get(camera_id)
    if entry is None:
        return False, 0.0
    online, fps, received = entry
    if time.monotonic() - received > get_settings().heartbeat_timeout_s:
        return False, 0.0
    return online, (round(fps, 1) if online else 0.0)


def clear() -> None:
    with _lock:
        _status.clear()
