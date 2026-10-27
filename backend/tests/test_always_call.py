"""P42: the Admin always gets a call, and the first sentence of a call is spoken at once."""

import pytest
from sqlalchemy import select

from app.config import get_settings
from app.models import Incident, User
from app.services import call_agent
from app.services.calls import CallService
from app.services.escalation import Escalation, ladder_for
from app.ws import manager
from tests.conftest import check_in, make_incident, twilio_post
from tests.test_call_replies import a_call, client, talk  # noqa: F401  (talk is a fixture)
from tests.test_escalation import FakeScheduler, timeline


@pytest.fixture
def ladder(roster, simulate, monkeypatch):
    check_in(roster, "Ravi Kumar")
    settings = get_settings()
    monkeypatch.setattr(settings, "always_call", "admin")
    return Escalation(scheduler=FakeScheduler(), call_service=CallService(settings), messenger=simulate,
                      settings=settings)


def user_id(db, name):
    return db.scalar(select(User.id).where(User.name == name))


def acknowledge(db, incident_id, name):
    incident = db.get(Incident, incident_id)
    incident.status, incident.acknowledged_by, incident.acknowledged_by_user_id = "acknowledged", name, user_id(db, name)
    db.commit()


def test_admin_step_never_waits_for_the_screen(ladder):
    steps = ladder_for("critical", ladder.settings, "fire")
    admin = next(st for st in steps if st.action == "call" and st.who == "admin")
    assert admin.delay_s == 0 and admin.defer_on_screen is False


def test_admin_on_screen_is_still_called_at_once(ladder, roster, monkeypatch):
    monkeypatch.setattr(manager, "online_user_ids", lambda: {user_id(roster, "Anita Rao")})
    incident_id = make_incident(roster, type_="fire", severity="critical")
    ladder.start(incident_id)
    ladder.scheduler.run(f"ladder:{incident_id}:2")
    assert ("call_placed", "Anita Rao") in timeline(roster, incident_id)


def test_head_guard_acknowledging_does_not_cancel_the_admin_call(ladder, roster):
    incident_id = make_incident(roster, type_="fire", severity="critical")
    ladder.start(incident_id)
    acknowledge(roster, incident_id, "Ravi Kumar")
    ladder.stop(incident_id)
    assert f"ladder:{incident_id}:2" in ladder.scheduler.jobs  # the Admin's call still goes out
    assert f"ladder:{incident_id}:3" not in ladder.scheduler.jobs  # the Manager's stops as before
    ladder.scheduler.run(f"ladder:{incident_id}:2")
    assert ("call_placed", "Anita Rao") in timeline(roster, incident_id)


def test_admin_who_acknowledged_is_not_called(ladder, roster):
    incident_id = make_incident(roster, type_="fire", severity="critical")
    ladder.start(incident_id)
    acknowledge(roster, incident_id, "Anita Rao")
    ladder.stop(incident_id)
    assert f"ladder:{incident_id}:2" not in ladder.scheduler.jobs


def test_resolved_stops_every_call(ladder, roster):
    incident_id = make_incident(roster, type_="fire", severity="critical")
    ladder.start(incident_id)
    roster.get(Incident, incident_id).status = "resolved"
    roster.commit()
    ladder.stop(incident_id)
    assert not [j for j in ladder.scheduler.jobs if j.startswith(f"ladder:{incident_id}:")]


def test_the_first_sentence_is_spoken_at_once(roster, talk, monkeypatch):  # noqa: F811
    monkeypatch.setattr(get_settings(), "call_opening_voice", "fast")
    call_id = a_call(roster, incident_id=make_incident(roster, type_="fire", severity="critical"))
    xml = twilio_post(client, f"/twilio/voice?call_id={call_id}", {}).text
    assert '<Say voice="Polly.Kajal-Neural"' in xml and "<Play>" not in xml  # nothing to download first


def test_admin_called_after_someone_has_it_hears_who(roster, talk, monkeypatch):  # noqa: F811
    monkeypatch.setattr(get_settings(), "call_opening_voice", "fast")
    incident_id = make_incident(roster, type_="fire", severity="critical")
    acknowledge(roster, incident_id, "Ravi Kumar")
    call_id = a_call(roster, incident_id=incident_id, name="Anita Rao")
    xml = twilio_post(client, f"/twilio/voice?call_id={call_id}", {}).text
    assert "There's a fire at" in xml and "Ravi Kumar is on it" in xml and "<Hangup/>" in xml
    call_agent._calls.clear()
