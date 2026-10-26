"""Calls depend on who is on the software: away = call now, on screen = 5 s to react first."""

import pytest
from sqlalchemy import select

from app.config import get_settings
from app.models import Incident, User
from app.services.calls import CallService
from app.services.escalation import Escalation, ladder_for
from app.ws import ConnectionManager, manager
from tests.conftest import check_in, make_incident
from tests.test_escalation import FakeScheduler, timeline


@pytest.fixture
def ladder(roster, simulate):
    check_in(roster, "Ravi Kumar")
    settings = get_settings()
    return Escalation(scheduler=FakeScheduler(), call_service=CallService(settings), messenger=simulate,
                      settings=settings)


def online(monkeypatch, db, *names):
    ids = {db.scalar(select(User.id).where(User.name == n)) for n in names}
    monkeypatch.setattr(manager, "online_user_ids", lambda: ids)


def test_ladders_use_the_onscreen_rule():
    s = get_settings()
    assert [(st.delay_s, st.action, st.who, st.defer_on_screen) for st in ladder_for("critical", s)] == [
        (0, "call", "head_guard", False), (0, "whatsapp", "admin", False),
        (0, "call", "admin", True), (5, "call", "manager", True)]
    assert [(st.delay_s, st.action, st.who, st.defer_on_screen) for st in ladder_for("high", s)] == [
        (0, "call", "head_guard", False), (0, "whatsapp", "admin", False), (0, "call", "manager", True)]


def test_admin_away_from_the_software_is_called_at_once(ladder, roster, monkeypatch):
    online(monkeypatch, roster)
    incident_id = make_incident(roster, type_="fire", severity="critical")
    ladder.start(incident_id)
    ladder.scheduler.run(f"ladder:{incident_id}:2")
    assert timeline(roster, incident_id)[-2:] == [("escalated", "Anita Rao"), ("call_placed", "Anita Rao")]
    assert f"ladder:{incident_id}:2" not in ladder.scheduler.jobs


def test_admin_on_screen_gets_the_grace_period(ladder, roster, monkeypatch):
    online(monkeypatch, roster, "Anita Rao")
    incident_id = make_incident(roster, type_="fire", severity="critical")
    ladder.start(incident_id)
    first = ladder.scheduler.jobs[f"ladder:{incident_id}:2"].run_date
    ladder.scheduler.run(f"ladder:{incident_id}:2")
    assert timeline(roster, incident_id) == [("detected", None)]
    job = ladder.scheduler.jobs[f"ladder:{incident_id}:2"]
    assert job.args == [incident_id, 2, True]
    assert round((job.run_date - first).total_seconds()) == 5


def test_ack_during_grace_skips_the_call(ladder, roster, monkeypatch):
    online(monkeypatch, roster, "Anita Rao")
    incident_id = make_incident(roster, type_="fire", severity="critical")
    ladder.start(incident_id)
    ladder.scheduler.run(f"ladder:{incident_id}:2")
    roster.get(Incident, incident_id).status = "acknowledged"
    roster.commit()
    ladder.scheduler.run(f"ladder:{incident_id}:2")
    assert timeline(roster, incident_id)[-1] == ("call_skipped_on_screen", "Anita Rao")
    assert ("call_placed", "Anita Rao") not in timeline(roster, incident_id)


def test_onscreen_but_no_reaction_is_called_after_grace(ladder, roster, monkeypatch):
    online(monkeypatch, roster, "Anita Rao")
    incident_id = make_incident(roster, type_="fire", severity="critical")
    ladder.start(incident_id)
    ladder.scheduler.run(f"ladder:{incident_id}:2")
    ladder.scheduler.run(f"ladder:{incident_id}:2")
    assert timeline(roster, incident_id)[-1] == ("call_placed", "Anita Rao")


def test_ws_manager_tracks_online_users():
    import asyncio

    class FakeSocket:
        async def accept(self):
            pass

    m = ConnectionManager()
    a, b = FakeSocket(), FakeSocket()
    asyncio.run(m.connect(a, lambda _: True, user_id=1))
    asyncio.run(m.connect(b, lambda _: True, user_id=2))
    m.disconnect(a)
    assert m.online_user_ids() == {2}


def test_ack_on_screen_records_the_cancelled_call(ladder, roster, monkeypatch):
    """Acknowledging removes the waiting call; the timeline still says it was cancelled, and who it was for."""
    online(monkeypatch, roster, "Anita Rao")
    incident_id = make_incident(roster, type_="fire", severity="critical")
    ladder.start(incident_id)
    ladder.scheduler.run(f"ladder:{incident_id}:2")  # Anita is on screen: her call now waits 5 s
    roster.get(Incident, incident_id).status = "acknowledged"
    roster.commit()
    ladder.stop(incident_id)
    assert ladder.scheduler.jobs == {}
    assert timeline(roster, incident_id)[-1] == ("call_skipped_on_screen", "Anita Rao")
