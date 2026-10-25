"""The escalation ladder: who gets called or messaged, and when, for each severity.

Critical (fire, fall, restricted entry at night):
    0 s   call the Head Guard for that camera; WhatsApp the Admin (with snapshot link)
    60 s  call the Admin
    2 min call the Manager
High (restricted entry by day, unattended bag, crowding):
    0 s   call the Head Guard; WhatsApp the Admin
    60 s  call the Manager
Low (ppe, loitering):
    0 s   WhatsApp the zone manager; no calls
    and if the same zone has 3 low incidents within 1 hour, call the Head Guard once

Each step is a timer job (APScheduler). When anyone acknowledges or closes the incident, stop()
removes the remaining jobs, drops queued calls and hangs up ringing ones. Each step also re-checks
the incident status before acting, so a step that slips through never calls after an acknowledgement.
"""

import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy import func, select

from app.config import PRODUCT_NAME, Settings, get_settings, load_cameras
from app.db import SessionLocal
from app.models import Incident
from app.services import camera_status
from app.services import incidents as incident_ops
from app.services.calls import CallService, calls
from app.services.directory import Contact, contacts_for_role
from app.services.badges import incident_badge_status
from app.services.permissions import share_url
from app.services.whatsapp import WhatsAppService, whatsapp
from app.ws import manager

log = logging.getLogger("safetyai.escalation")

TYPE_LABELS = {
    "access": "Restricted area entry", "ppe": "Missing helmet or vest", "fall": "Person down, possible fall",
    "fire": "Fire or smoke", "unattended": "Unattended bag", "loitering": "Loitering", "crowding": "Crowding",
}


@dataclass(frozen=True)
class Step:
    delay_s: float
    action: str  # call, whatsapp
    who: str  # head_guard, admin, manager, zone_manager


def ladder_for(severity: str, s: Settings) -> list[Step]:
    if severity == "critical":
        return [Step(0, "call", "head_guard"), Step(0, "whatsapp", "admin"),
                Step(s.critical_admin_delay_s, "call", "admin"), Step(s.critical_manager_delay_s, "call", "manager")]
    if severity == "high":
        return [Step(0, "call", "head_guard"), Step(0, "whatsapp", "admin"),
                Step(s.high_manager_delay_s, "call", "manager")]
    return [Step(0, "whatsapp", "zone_manager")]


def local_time_text(dt: datetime, tz: str) -> str:
    return dt.astimezone(ZoneInfo(tz)).strftime("%I:%M %p").lstrip("0")


def alert_text(incident: Incident, tz: str, badge: str | None = None) -> str:
    where = incident.camera.name + (f", {incident.zone.replace('-', ' ')}" if incident.zone else "")
    note = " No badge scanned for this entry." if badge == "not_scanned" else ""
    return (f"{PRODUCT_NAME} alert. {incident.severity.capitalize()}: {TYPE_LABELS.get(incident.type, incident.type)} "
            f"at {where}, {local_time_text(incident.started_at, tz)}. Incident {incident.id}.{note}")


class Escalation:
    def __init__(self, scheduler: BackgroundScheduler | None = None, call_service: CallService | None = None,
                 messenger: WhatsAppService | None = None, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.scheduler = scheduler or BackgroundScheduler(timezone="UTC")
        self.calls = call_service or calls
        self.whatsapp = messenger or whatsapp
        self._repeat_called: dict[tuple[str, str], float] = {}  # (camera, zone) -> monotonic time of repeat call
        self._silent_alerted: set[str] = set()
        self._lock = threading.Lock()

    # ---------- lifecycle (API startup/shutdown) ----------

    def start_background(self) -> None:
        if not self.scheduler.running:
            self.scheduler.start()
        hour, minute = (int(x) for x in self.settings.digest_time.split(":"))
        self.scheduler.add_job(self.send_digest, "cron", hour=hour, minute=minute,
                               timezone=ZoneInfo(self.settings.timezone), id="daily-digest", replace_existing=True)
        self.scheduler.add_job(self.check_heartbeats, "interval", seconds=self.settings.heartbeat_check_s,
                               id="heartbeat-watchdog", replace_existing=True)

    def shutdown(self) -> None:
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)

    # ---------- ladder ----------

    def start(self, incident_id: int) -> None:
        """Schedule every step. Returns at once; steps run on the scheduler's threads."""
        with SessionLocal() as db:
            incident = incident_ops.load(db, incident_id)
            if incident is None:
                return
            severity = incident.severity
        now = datetime.now(timezone.utc)
        for index, step in enumerate(ladder_for(severity, self.settings)):
            self.scheduler.add_job(self.run_step, "date", run_date=now + timedelta(seconds=step.delay_s),
                                   args=[incident_id, index], id=f"ladder:{incident_id}:{index}",
                                   replace_existing=True, misfire_grace_time=60)
        if severity == "low":
            self.scheduler.add_job(self.check_low_repeat, "date", run_date=now, args=[incident_id],
                                   id=f"repeat:{incident_id}", replace_existing=True, misfire_grace_time=60)
        log.info("incident=%d step=ladder_scheduled severity=%s", incident_id, severity)

    def stop(self, incident_id: int, except_call_id: int | None = None) -> None:
        """Someone acknowledged or closed it: no more calls for this incident."""
        prefix = f"ladder:{incident_id}:"
        for job in self.scheduler.get_jobs():
            if job.id.startswith(prefix):
                job.remove()
        cancelled = self.calls.cancel_pending(incident_id)
        hung = self.calls.hang_up_active(incident_id, except_call_id) if self.calls.mode == "live" else 0
        log.info("incident=%d step=ladder_stopped queued_cancelled=%d hung_up=%d", incident_id, cancelled, hung)

    def _contacts(self, db, who: str, incident: Incident) -> list[Contact]:
        s = self.settings
        if who == "zone_manager":
            managers = contacts_for_role(db, s.role_manager, incident.camera_id, incident.zone)
            in_zone = [c for c in managers if c.zone == incident.zone]
            return (in_zone or managers)[:1]
        role = {"head_guard": s.role_head_guard, "admin": s.role_admin, "manager": s.role_manager}[who]
        return contacts_for_role(db, role, incident.camera_id, incident.zone)

    def run_step(self, incident_id: int, index: int) -> None:
        t0 = time.perf_counter()
        with SessionLocal() as db:
            incident = incident_ops.load(db, incident_id)
            if incident is None:
                return
            if incident.status != "open":
                log.info("incident=%d step=%d skipped: already %s", incident_id, index, incident.status)
                return
            step = ladder_for(incident.severity, self.settings)[index]
            contacts = self._contacts(db, step.who, incident)
            if not contacts:
                log.warning("incident=%d step=%d no %s with a phone number on the roster", incident_id, index,
                            step.who)
                return
            badge = incident_badge_status(db, incident) if incident.type == "access" else None
            text = alert_text(incident, self.settings.timezone, badge)
            if step.action == "call":
                target = contacts[0]
                if index > 0:
                    incident_ops.add_timeline(incident, "escalated", to=target.name)
                    db.commit()
                    manager.publish_from_thread(incident_ops.to_message(incident))
                self.calls.place(kind="alert", to_name=target.name, to_phone=target.phone,
                                 responder_id=target.responder_id, incident_id=incident_id)
            else:
                image = share_url(incident.snapshot_path) if incident.snapshot_path else None
                for contact in contacts:
                    if self.whatsapp.send(contact.phone, text, image):
                        incident_ops.add_timeline(incident, "whatsapp_sent", to=contact.name)
                db.commit()
                manager.publish_from_thread(incident_ops.to_message(incident))
        log.info("incident=%d step=%d action=%s who=%s ms=%.1f", incident_id, index, step.action, step.who,
                 (time.perf_counter() - t0) * 1000)

    # ---------- low severity: repeated zone ----------

    def check_low_repeat(self, incident_id: int) -> None:
        """3 low incidents in the same zone within an hour: call the Head Guard, once per zone per hour."""
        s = self.settings
        with SessionLocal() as db:
            incident = incident_ops.load(db, incident_id)
            if incident is None or incident.zone is None:
                return
            # this incident and the ones before it, within the window (never later ones)
            since = incident.started_at - timedelta(minutes=s.low_repeat_window_min)
            count = db.scalar(select(func.count(Incident.id)).where(
                Incident.camera_id == incident.camera_id, Incident.zone == incident.zone,
                Incident.severity == "low", Incident.started_at >= since,
                Incident.started_at <= incident.started_at))
            if count < s.low_repeat_count:
                return
            key = (incident.camera_id, incident.zone)
            with self._lock:
                last = self._repeat_called.get(key)
                if last is not None and time.monotonic() - last < s.low_repeat_window_min * 60:
                    return
                self._repeat_called[key] = time.monotonic()
            contacts = contacts_for_role(db, s.role_head_guard, incident.camera_id, incident.zone)
            if not contacts:
                log.warning("incident=%d repeat in %s but no Head Guard on the roster", incident_id, incident.zone)
                return
            target = contacts[0]
            log.info("incident=%d zone=%s repeated %d times in %d min: calling %s", incident_id, incident.zone,
                     count, s.low_repeat_window_min, target.name)
        self.calls.place(kind="alert", to_name=target.name, to_phone=target.phone,
                         responder_id=target.responder_id, incident_id=incident_id)

    # ---------- daily digest and heartbeat watchdog ----------

    def digest_text(self, db) -> str | None:
        since = datetime.now(timezone.utc) - timedelta(hours=self.settings.digest_lookback_h)
        rows = db.execute(select(Incident.type, Incident.zone, func.count(Incident.id))
                          .where(Incident.severity == "low", Incident.started_at >= since)
                          .group_by(Incident.type, Incident.zone)).all()
        if not rows:
            return None
        parts = [f"{n} {TYPE_LABELS.get(t, t).lower()} in {(z or 'no zone').replace('-', ' ')}" for t, z, n in rows]
        return f"{PRODUCT_NAME} daily digest (last {self.settings.digest_lookback_h} h): " + "; ".join(parts) + "."

    def send_digest(self) -> int:
        with SessionLocal() as db:
            text = self.digest_text(db)
            if text is None:
                log.info("digest: no low-severity incidents")
                return 0
            managers = contacts_for_role(db, self.settings.role_manager)
        return self.whatsapp.send_many([(c.phone, text, None) for c in managers])

    def check_heartbeats(self) -> None:
        """A camera that was reporting and went silent: WhatsApp the Admin once (again only after it recovers)."""
        names = {cam.id: cam.name for cam in load_cameras()}
        for camera_id in camera_status.known_cameras():
            online, _ = camera_status.get(camera_id)
            if online:
                self._silent_alerted.discard(camera_id)
                continue
            if camera_id in self._silent_alerted:
                continue
            self._silent_alerted.add(camera_id)
            text = (f"{PRODUCT_NAME}: camera {names.get(camera_id, camera_id)} has been silent for "
                    f"{self.settings.heartbeat_timeout_s} s. Check the camera and its worker.")
            log.warning("camera=%s heartbeat lost: messaging admins", camera_id)
            with SessionLocal() as db:
                admins = contacts_for_role(db, self.settings.role_admin)
            self.whatsapp.send_many([(c.phone, text, None) for c in admins])


escalation = Escalation()
