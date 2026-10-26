"""The escalation ladder: who gets called or messaged, and when, for each severity.

Critical (fire, fall, restricted entry at night):
    0 s   call the Head Guard for that camera; WhatsApp the Admin (with snapshot link)
    0 s   call the Admin           (5 s if the Admin has a dashboard open: time to react on screen)
    5 s   call the Manager         (10 s if the Manager has a dashboard open)
High (restricted entry by day, unattended bag, crowding):
    0 s   call the Head Guard; WhatsApp the Admin
    0 s   call the Manager         (5 s if on screen)
Someone on the software sees the red alert first; acknowledging within ONSCREEN_GRACE_S cancels their call
(timeline: call_skipped_on_screen). Someone away from it is called at once.
Low (ppe, loitering):
    0 s   WhatsApp the zone manager; no calls
    and if the same zone has 3 low incidents within 1 hour, call the Head Guard once

Duty (P23): Head Guards and Guards are called only while checked in on duty. If nobody of that role is on
duty, the shift roster is called instead, the timeline gets "duty_fallback" and the Head Guards are told
on WhatsApp once per incident, so nothing is silently dropped.

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

from app.config import PRODUCT_NAME, Settings, get_settings
from app.db import SessionLocal
from app.models import Incident
from app.services import camera_registry, camera_status
from app.services import incidents as incident_ops
from app.services.calls import CallService, calls
from app.services.directory import Contact, contacts_for_role, contacts_on_duty
from app.services.badges import incident_badge_status
from app.services.permissions import share_url
from app.services.whatsapp import WhatsAppService, whatsapp
from app.ws import manager

log = logging.getLogger("safetyai.escalation")

TYPE_LABELS = {
    "access": "Restricted area entry", "ppe": "Missing helmet or vest", "fall": "Person down, possible fall",
    "fire": "Fire", "smoke": "Smoke", "no_helmet": "Rider without a helmet", "unattended": "Unattended bag",
    "loitering": "Loitering", "crowding": "Crowding",
}


@dataclass(frozen=True)
class Step:
    delay_s: float
    action: str  # call, whatsapp, alert_all
    who: str  # head_guard, admin, manager, zone_manager, guards
    defer_on_screen: bool = False  # the target has a dashboard open: wait ONSCREEN_GRACE_S before calling


def ladder_for(severity: str, s: Settings, kind: str | None = None) -> list[Step]:
    if kind == "no_helmet":  # a rider without a helmet at a gate: just the guard of that gate's camera, at once
        return [Step(0, "call", "gate_guard")]
    # Critical and high call the whole hierarchy; if nobody has acknowledged after NO_ANSWER_ALERT_ALL_S,
    # every guard on shift gets a WhatsApp and a call that tells them the problem (roll call).
    nobody = Step(s.no_answer_alert_all_s, "alert_all", "guards")
    if severity == "critical":
        return [Step(0, "call", "head_guard"), Step(0, "whatsapp", "admin"),
                Step(0, "call", "admin", True), Step(s.onscreen_grace_s, "call", "manager", True), nobody]
    if severity == "high":
        return [Step(0, "call", "head_guard"), Step(0, "whatsapp", "admin"), Step(0, "call", "manager", True),
                Step(0, "call", "admin", True), nobody]
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
        self._duty_warned: set[int] = set()  # incidents whose Head Guards were already told nobody is on duty
        self._waiting: dict[str, str] = {}  # job id of a call held back for someone on screen -> their name
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
            severity, kind = incident.severity, incident.type
        now = datetime.now(timezone.utc)
        for index, step in enumerate(ladder_for(severity, self.settings, kind)):
            self.scheduler.add_job(self.run_step, "date", run_date=now + timedelta(seconds=step.delay_s),
                                   args=[incident_id, index], id=f"ladder:{incident_id}:{index}",
                                   replace_existing=True, misfire_grace_time=60)
        if severity == "low" and kind != "no_helmet":
            self.scheduler.add_job(self.check_low_repeat, "date", run_date=now, args=[incident_id],
                                   id=f"repeat:{incident_id}", replace_existing=True, misfire_grace_time=60)
        log.info("incident=%d step=ladder_scheduled severity=%s", incident_id, severity)

    def stop(self, incident_id: int, except_call_id: int | None = None) -> None:
        """Someone acknowledged or closed it: no more calls for this incident."""
        prefix = f"ladder:{incident_id}:"
        skipped = []
        for job in self.scheduler.get_jobs():
            if job.id.startswith(prefix):
                job.remove()
                with self._lock:
                    name = self._waiting.pop(job.id, None)
                if name:
                    skipped.append(name)  # they reacted on screen within the grace period: the call never happens
        if skipped:
            with SessionLocal() as db:
                incident = incident_ops.load(db, incident_id)
                if incident is not None:
                    for name in skipped:
                        incident_ops.add_timeline(incident, "call_skipped_on_screen", to=name)
                    db.commit()
                    manager.publish_from_thread(incident_ops.to_message(incident))
        cancelled = self.calls.cancel_pending(incident_id)
        hung = self.calls.hang_up_active(incident_id, except_call_id) if self.calls.mode == "live" else 0
        log.info("incident=%d step=ladder_stopped queued_cancelled=%d hung_up=%d", incident_id, cancelled, hung)

    def _contacts(self, db, who: str, incident: Incident) -> tuple[list[Contact], bool]:
        """(contacts, fallback): fallback=True when nobody of that role was on duty."""
        s = self.settings
        if who == "zone_manager":
            managers = contacts_for_role(db, s.role_manager, incident.camera_id, incident.zone)
            in_zone = [c for c in managers if c.zone == incident.zone]
            return (in_zone or managers)[:1], False
        if who == "gate_guard":
            # the guard of the gate this camera watches: a Guard who has this camera, the one in this zone first;
            # nobody (on duty) with this camera -> that camera's Head Guard
            guards, fallback = contacts_on_duty(db, s.role_guard, incident.camera_id, incident.zone)
            if guards:
                in_zone = [c for c in guards if incident.zone and c.zone == incident.zone]
                return (in_zone or guards)[:1], fallback
            return contacts_on_duty(db, s.role_head_guard, incident.camera_id, incident.zone)
        role = {"head_guard": s.role_head_guard, "admin": s.role_admin, "manager": s.role_manager}[who]
        return contacts_on_duty(db, role, incident.camera_id, incident.zone)

    def _duty_fallback(self, db, incident: Incident, who: str, target: Contact) -> None:
        """Nobody of this role was on duty: note it on the timeline and tell the Head Guards (once per incident)."""
        label = who.replace("_", " ")
        incident_ops.add_timeline(incident, "duty_fallback", to=target.name)
        log.warning("incident=%d nobody on duty as %s: calling %s from the shift roster", incident.id, label,
                    target.name)
        with self._lock:
            if incident.id in self._duty_warned:
                return
            self._duty_warned.add(incident.id)
        head_guards = contacts_for_role(db, self.settings.role_head_guard, incident.camera_id, incident.zone)
        text = (f"{PRODUCT_NAME} warning: nobody is checked in on duty as {label} for {incident.camera.name}. "
                f"Calling {target.name} from the shift roster for incident {incident.id}. Please check the team.")
        for contact in head_guards:
            if self.whatsapp.send(contact.phone, text, None):
                incident_ops.add_timeline(incident, "whatsapp_sent", to=contact.name)

    def run_step(self, incident_id: int, index: int, deferred: bool = False) -> None:
        """deferred=True: the second run of an on-screen step, after the grace period."""
        t0 = time.perf_counter()
        if deferred:
            with self._lock:
                self._waiting.pop(f"ladder:{incident_id}:{index}", None)
        with SessionLocal() as db:
            incident = incident_ops.load(db, incident_id)
            if incident is None:
                return
            step = ladder_for(incident.severity, self.settings, incident.type)[index]
            if incident.status != "open":
                log.info("incident=%d step=%d skipped: already %s", incident_id, index, incident.status)
                if deferred:
                    contacts, _ = self._contacts(db, step.who, incident)
                    if contacts:
                        incident_ops.add_timeline(incident, "call_skipped_on_screen", to=contacts[0].name)
                        db.commit()
                        manager.publish_from_thread(incident_ops.to_message(incident))
                return
            if step.action == "alert_all":
                self._nobody_answered(db, incident)
                return
            contacts, fallback = self._contacts(db, step.who, incident)
            if not contacts:
                log.warning("incident=%d step=%d no %s with a phone number on the roster", incident_id, index,
                            step.who)
                return
            if step.defer_on_screen and not deferred and contacts[0].user_id in manager.online_user_ids():
                run_at = datetime.now(timezone.utc) + timedelta(seconds=self.settings.onscreen_grace_s)
                job_id = f"ladder:{incident_id}:{index}"
                self.scheduler.add_job(self.run_step, "date", run_date=run_at, args=[incident_id, index, True],
                                       id=job_id, replace_existing=True, misfire_grace_time=60)
                with self._lock:
                    self._waiting[job_id] = contacts[0].name
                log.info("incident=%d step=%d %s is on screen: calling in %.0f s unless acknowledged",
                         incident_id, index, contacts[0].name, self.settings.onscreen_grace_s)
                return
            badge = incident_badge_status(db, incident) if incident.type == "access" else None
            text = alert_text(incident, self.settings.timezone, badge)
            if step.action == "call":
                target = contacts[0]
                if index > 0:
                    incident_ops.add_timeline(incident, "escalated", to=target.name)
                if fallback:
                    self._duty_fallback(db, incident, step.who, target)
                if index > 0 or fallback:
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

    def _nobody_answered(self, db, incident: Incident) -> None:
        """Still open after the whole ladder: alert every guard on shift, telling them what happened."""
        from app.services import alerts  # alerts -> calls; imported here to keep the module graph simple

        badge = incident_badge_status(db, incident) if incident.type == "access" else None
        text = alert_text(incident, self.settings.timezone, badge)
        incident_ops.add_timeline(incident, "nobody_answered")
        db.commit()
        manager.publish_from_thread(incident_ops.to_message(incident))
        log.warning("incident=%d nobody acknowledged in %.0f s: alerting every guard on shift", incident.id,
                    self.settings.no_answer_alert_all_s)
        notice = alerts.alert_all(text, by_name=PRODUCT_NAME, user=None, channel="phone", incident_id=incident.id,
                                  via="")
        manager.publish_from_thread(notice)
        if notice.get("incident"):
            manager.publish_from_thread(notice["incident"])

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
            contacts, fallback = contacts_on_duty(db, s.role_head_guard, incident.camera_id, incident.zone)
            if not contacts:
                log.warning("incident=%d repeat in %s but no Head Guard on the roster", incident_id, incident.zone)
                return
            target = contacts[0]
            if fallback:
                self._duty_fallback(db, incident, "head_guard", target)
                db.commit()
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
        names = camera_registry.names()
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
