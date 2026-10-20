"""Background thread: every few seconds tell the API "camera X is online at N analysed fps".

If the worker crashes, the heartbeats stop and the API marks the camera offline after
HEARTBEAT_TIMEOUT_S. A failed post is only logged: the next one is a few seconds away.
"""

import logging
import threading
from collections.abc import Callable
from datetime import datetime, timezone

import httpx

from app.config import Settings

log = logging.getLogger("safetyai.heartbeat")

POST_TIMEOUT_S = 2.0


class HeartbeatSender:
    def __init__(self, camera_id: str, settings: Settings, status: Callable[[], tuple[bool, float]]) -> None:
        self.camera_id = camera_id
        self.settings = settings
        self.status = status  # returns (online, analysed fps) right now
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="heartbeat", daemon=True)
        self._failing = False

    def start(self) -> "HeartbeatSender":
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        url = f"{self.settings.api_base_url}/internal/heartbeat"
        headers = {"X-Internal-Key": self.settings.internal_key}
        while not self._stop.wait(self.settings.worker_heartbeat_s):
            online, fps = self.status()
            body = {"camera_id": self.camera_id, "fps": round(fps, 1), "online": online,
                    "ts": datetime.now(timezone.utc).isoformat()}
            try:
                httpx.post(url, json=body, headers=headers, timeout=POST_TIMEOUT_S).raise_for_status()
                if self._failing:
                    log.info("camera=%s heartbeat reaching the API again", self.camera_id)
                self._failing = False
            except httpx.HTTPError as exc:
                if not self._failing:  # log once, not every 5 s
                    log.warning("camera=%s heartbeat failed: %s", self.camera_id, exc)
                self._failing = True
