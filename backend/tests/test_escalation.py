"""The call ladder, with a fake timer (no waiting) and calls/WhatsApp in simulate mode."""


import pytest

from app.config import get_settings
from app.models import Call, Incident, RollCall
from app.services.calls import CallService
from app.services.escalation import Escalation, ladder_for
from tests.conftest import check_in, make_incident


class FakeJob:
    def __init__(self, scheduler, job_id, func, args, run_date):
        self.scheduler, self.id, self.func, self.args, self.run_date = scheduler, job_id, func, args, run_date

    def remove(self):
        self.scheduler.jobs.pop(self.id, None)


class FakeScheduler:
    """Records jobs instead of waiting; tests run them by hand."""

    running = True

    def __init__(self):
        self.jobs = {}

    def add_job(self, func, trigger, run_date=None, args=(), id=None, **_):
        self.jobs[id] = FakeJob(self, id, func, list(args), run_date)

    def get_jobs(self):
        return list(self.jobs.values())

    def run(self, job_id):
        job = self.jobs.pop(job_id)
        job.func(*job.args)


@pytest.fixture
def ladder(roster, simulate):
    settings = get_settings()
    return Escalation(scheduler=FakeScheduler(), call_service=CallService(settings),
                      messenger=simulate, settings=settings)


def timeline(db, incident_id):
    db.expire_all()
    return [(e.action, e.target) for e in db.get(Incident, incident_id).timeline]


def test_ladders_match_the_rules():
    s = get_settings()
    assert [(st.delay_s, st.action, st.who) for st in ladder_for("critical", s)] == [
        (0, "call", "head_guard"), (0, "whatsapp", "admin"), (0, "call", "admin"), (5, "call", "manager"),
        (45, "alert_all", "guards")]
    assert [(st.delay_s, st.action, st.who) for st in ladder_for("high", s)] == [
        (0, "call", "head_guard"), (0, "whatsapp", "admin"), (0, "call", "manager"), (0, "call", "admin"),
        (45, "alert_all", "guards")]
    assert [(st.action, st.who) for st in ladder_for("low", s)] == [("whatsapp", "zone_manager")]


def test_critical_ladder_calls_head_guard_then_admin_then_manager(ladder, roster):
    check_in(roster, "Ravi Kumar")  # the Head Guard is on duty (see test_duty.py for the off-duty fallback)
    incident_id = make_incident(roster, type_="fire", severity="critical")
    ladder.start(incident_id)
    jobs = ladder.scheduler.jobs
    assert sorted(jobs) == [f"ladder:{incident_id}:{i}" for i in range(5)]
    delays = sorted(round((j.run_date - jobs[f"ladder:{incident_id}:0"].run_date).total_seconds())
                    for j in jobs.values())
    assert delays == [0, 0, 0, 5, 45]  # nobody on screen: Admin at once, Manager 5 s later, all guards at 45 s
    for i in range(4):
        ladder.scheduler.run(f"ladder:{incident_id}:{i}")
    assert timeline(roster, incident_id) == [
        ("detected", None), ("call_placed", "Ravi Kumar"), ("whatsapp_sent", "Anita Rao"),
        ("escalated", "Anita Rao"), ("call_placed", "Anita Rao"),
        ("escalated", "Vikram Shah"), ("call_placed", "Vikram Shah")]
    assert ladder.whatsapp.sent[0][0] == "+919800000001"


def test_acknowledging_stops_the_ladder(ladder, roster):
    incident_id = make_incident(roster, type_="fall", severity="critical")
    ladder.start(incident_id)
    ladder.scheduler.run(f"ladder:{incident_id}:0")
    incident = roster.get(Incident, incident_id)
    incident.status = "acknowledged"
    roster.commit()
    ladder.stop(incident_id)
    assert ladder.scheduler.jobs == {}  # admin and manager steps removed


def test_step_that_slips_through_after_ack_does_nothing(ladder, roster):
    incident_id = make_incident(roster, type_="fall", severity="critical")
    incident = roster.get(Incident, incident_id)
    incident.status = "acknowledged"
    roster.commit()
    ladder.run_step(incident_id, 2)
    assert timeline(roster, incident_id) == [("detected", None)]


def test_high_ladder_calls_the_whole_hierarchy(ladder, roster):
    incident_id = make_incident(roster, type_="unattended", severity="high")
    ladder.start(incident_id)
    for i in range(4):  # everything before the 45 s "nobody answered" step
        ladder.scheduler.run(f"ladder:{incident_id}:{i}")
    called = [c.to_name for c in roster.query(Call).filter_by(incident_id=incident_id).order_by(Call.id)]
    assert called == ["Ravi Kumar", "Vikram Shah", "Anita Rao"]


def test_nobody_answering_alerts_every_guard_with_the_problem(ladder, roster):
    incident_id = make_incident(roster, type_="fire", severity="critical")
    ladder.start(incident_id)
    for job_id in sorted(ladder.scheduler.jobs):
        ladder.scheduler.run(job_id)
    actions = timeline(roster, incident_id)
    assert ("nobody_answered", None) in actions and ("alert_all", None) in actions
    roll = roster.query(RollCall).one()
    assert roll.started_by == "RingGuard" and roll.message.startswith("RingGuard alert. Critical: Fire")
    guards = roster.query(Call).filter_by(kind="roll_call").all()
    assert len(guards) == 4 and all(c.message == roll.message for c in guards)
    assert any("Nobody answered" in text and "Fire" in text for _, text, _ in ladder.whatsapp.sent)


def test_acknowledging_in_time_means_no_alert_all(ladder, roster):
    incident_id = make_incident(roster, type_="fire", severity="critical")
    ladder.start(incident_id)
    ladder.scheduler.run(f"ladder:{incident_id}:0")
    incident = roster.get(Incident, incident_id)
    incident.status = "acknowledged"
    roster.commit()
    ladder.run_step(incident_id, 4)  # even if the 45 s step slipped through
    assert roster.query(RollCall).count() == 0


def test_low_incident_only_messages_the_zone_manager(ladder, roster):
    incident_id = make_incident(roster, type_="loitering", severity="low", zone="loading-bay",
                                camera_id="cam-phone")
    ladder.start(incident_id)
    for job_id in sorted(ladder.scheduler.jobs):
        ladder.scheduler.run(job_id)
    assert roster.query(Call).count() == 0
    assert [to for to, _, _ in ladder.whatsapp.sent] == ["+919800000002"]


def test_third_low_incident_in_an_hour_calls_the_head_guard_once(ladder, roster):
    ids = [make_incident(roster, type_="ppe", severity="low", zone="helmet-area", camera_id="cam-phone",
                         minutes_ago=m) for m in (40, 20, 1)]
    for incident_id in ids:
        ladder.check_low_repeat(incident_id)
    ladder.check_low_repeat(make_incident(roster, type_="ppe", severity="low", zone="helmet-area",
                                          camera_id="cam-phone"))
    calls = roster.query(Call).all()
    assert [(c.to_name, c.incident_id) for c in calls] == [("Ravi Kumar", ids[2])]


def test_missing_roster_is_logged_not_crashed(ladder, roster):
    roster.execute(Call.__table__.delete())
    from app.models import Responder
    roster.query(Responder).delete()
    roster.commit()
    incident_id = make_incident(roster, type_="fire", severity="critical")
    ladder.run_step(incident_id, 0)
    assert timeline(roster, incident_id) == [("detected", None)]


def test_daily_digest_lists_low_incidents(ladder, roster):
    make_incident(roster, type_="ppe", severity="low", zone="helmet-area", camera_id="cam-phone")
    make_incident(roster, type_="ppe", severity="low", zone="helmet-area", camera_id="cam-phone")
    make_incident(roster, type_="fire", severity="critical")
    assert ladder.send_digest() == 1  # one manager on the roster
    text = ladder.whatsapp.sent[0][1]
    assert "2 missing helmet or vest in helmet area" in text and "fire" not in text.lower()


def test_silent_camera_messages_the_admin_once(ladder, roster, monkeypatch):
    from app.services import camera_status
    camera_status.update("cam-webcam", True, 9.0)
    ladder.check_heartbeats()
    assert ladder.whatsapp.sent == []
    monkeypatch.setattr(camera_status, "get", lambda camera_id: (False, 0.0))
    ladder.check_heartbeats()
    ladder.check_heartbeats()
    assert len(ladder.whatsapp.sent) == 1 and "silent" in ladder.whatsapp.sent[0][1]


def test_rider_without_a_helmet_calls_only_the_gate_guard_of_that_camera(ladder, roster):
    check_in(roster, "Suresh Babu")  # the Guard who has cam-webcam
    incident_id = make_incident(roster, type_="no_helmet", severity="low")
    ladder.start(incident_id)
    assert sorted(ladder.scheduler.jobs) == [f"ladder:{incident_id}:0"]  # no hierarchy, no repeat check
    ladder.scheduler.run(f"ladder:{incident_id}:0")
    called = [c.to_name for c in roster.query(Call).filter_by(incident_id=incident_id)]
    assert called == ["Suresh Babu"]
