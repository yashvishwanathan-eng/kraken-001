"""Turn a confirmed rule candidate into a stored incident.

Order matters for speed:
  1. insert the incident + a "detected" timeline row (milliseconds)
  2. save the snapshot JPEG
  3. tell the API (POST /internal/incidents/{id}/created) so the dashboard sees it in under 2 s
  4. in the background, wait until 5 s after the event, then write the 10 s clip from the ring buffer

Nothing here may crash the worker: database, disk or API failures are logged and detection goes on.
"""

import logging
import threading
import time
from datetime import datetime, timezone

import httpx

from app.config import Settings, media_root
from app.db import SessionLocal
from app.models import Incident, TimelineEvent
from worker.buffer import RingBuffer, write_clip
from worker.rules import Candidate

log = logging.getLogger("safetyai.incidents")

NOTIFY_TIMEOUT_S = 5.0
NOTIFY_ATTEMPTS = 2  # one try + one retry (CLAUDE.md reliability rule)


class IncidentReporter:
    def __init__(self, camera_id: str, settings: Settings, ring: RingBuffer) -> None:
        self.camera_id = camera_id
        self.settings = settings
        self.ring = ring
        self.media = media_root()

    def report(self, candidate: Candidate, snapshot_jpeg: bytes, t_event: float) -> int | None:
        """Store the incident. `t_event` is the monotonic time it fired (used to cut the clip)."""
        t0 = time.perf_counter()
        try:
            incident_id = self._insert(candidate)
        except Exception:
            log.exception("camera=%s step=insert failed for %s", self.camera_id, candidate.type)
            return None
        insert_ms = (time.perf_counter() - t0) * 1000
        try:
            (self.media / f"{incident_id}.jpg").write_bytes(snapshot_jpeg)
        except OSError:
            log.exception("incident=%d step=snapshot failed", incident_id)
        log.info("incident=%d type=%s severity=%s zone=%s step=stored ms=%.1f reason=%s",
                 incident_id, candidate.type, candidate.severity, candidate.zone, insert_ms, candidate.reason)
        threading.Thread(target=self._notify_api, args=(incident_id,), daemon=True).start()
        threading.Thread(target=self._finish_clip, args=(incident_id, t_event), daemon=True).start()
        return incident_id

    def _insert(self, candidate: Candidate) -> int:
        now = datetime.now(timezone.utc)
        with SessionLocal() as db:
            incident = Incident(
                type=candidate.type, severity=candidate.severity, status="open", camera_id=self.camera_id,
                zone=candidate.zone, started_at=now, confidence=candidate.confidence,
            )
            db.add(incident)
            db.flush()  # assigns incident.id
            incident.snapshot_path = f"{incident.id}.jpg"
            incident.clip_path = f"{incident.id}.mp4"
            db.add(TimelineEvent(incident_id=incident.id, at=now, action="detected"))
            db.commit()
            return incident.id

    def _notify_api(self, incident_id: int) -> None:
        url = f"{self.settings.api_base_url}/internal/incidents/{incident_id}/created"
        headers = {"X-Internal-Key": self.settings.internal_key}
        for attempt in range(1, NOTIFY_ATTEMPTS + 1):
            t0 = time.perf_counter()
            try:
                response = httpx.post(url, headers=headers, timeout=NOTIFY_TIMEOUT_S)
                response.raise_for_status()
                log.info("incident=%d step=notify_api ms=%.1f", incident_id, (time.perf_counter() - t0) * 1000)
                return
            except httpx.HTTPError as exc:
                log.warning("incident=%d step=notify_api attempt=%d failed: %s", incident_id, attempt, exc)
        log.error("incident=%d step=notify_api gave up; incident is stored but the dashboard was not told", incident_id)

    def _finish_clip(self, incident_id: int, t_event: float) -> None:
        wait = t_event + self.settings.clip_after_s - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        frames = self.ring.between(t_event - self.settings.clip_before_s, t_event + self.settings.clip_after_s)
        t0 = time.perf_counter()
        ok = write_clip(frames, self.media / f"{incident_id}.mp4")
        log.info("incident=%d step=clip ok=%s frames=%d ms=%.1f", incident_id, ok, len(frames),
                 (time.perf_counter() - t0) * 1000)
        if not ok:
            try:
                with SessionLocal() as db:
                    incident = db.get(Incident, incident_id)
                    if incident is not None:
                        incident.clip_path = None  # no clip: the dashboard shows the snapshot only
                        db.commit()
            except Exception:
                log.exception("incident=%d step=clip_cleanup failed", incident_id)
