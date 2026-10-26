"""Friday on the phone: a short conversation (CALL_STYLE=conversation) instead of a keypad menu."""

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app
from app.models import AuditLog, Call, Incident, Responder, RollCall
from app.routers import twilio as twilio_router
from app.services import call_agent, tts
from tests.conftest import make_incident, twilio_post

client = TestClient(app)


@pytest.fixture
def talk(twilio_on, monkeypatch):
    monkeypatch.setattr(get_settings(), "call_style", "conversation")
    sent = []
    monkeypatch.setattr(twilio_router, "_publish", lambda m: m is not None and sent.append(m))
    monkeypatch.setattr(call_agent, "_llm_turn", lambda conv: None)  # keyword fallback unless a test sets an LLM
    yield sent
    call_agent._calls.clear()


def a_call(db, kind="alert", incident_id=None, name="Ravi Kumar", message=None):
    responder = db.query(Responder).filter_by(name=name).one()
    call = Call(kind=kind, incident_id=incident_id, responder_id=responder.id, to_name=name,
                to_phone=responder.phone, message=message, status="in-progress")
    db.add(call)
    db.commit()
    return call.id


def say_(call_id, words="", digits=""):
    form = {"SpeechResult": words} if words else {}
    if digits:
        form["Digits"] = digits
    return twilio_post(client, f"/twilio/talk?call_id={call_id}", form).text


def test_the_call_opens_like_a_person_and_listens(roster, talk):
    call_id = a_call(roster, incident_id=make_incident(roster, type_="fire", severity="critical"))
    opening = twilio_post(client, f"/twilio/voice?call_id={call_id}", {}).text
    assert "Hi Ravi, this is Friday" in opening and "There&apos;s a fire" in opening.replace("'", "&apos;")
    assert "Where are you right now?" in opening and "Press" not in opening and "alert." not in opening
    assert "/twilio/talk?call_id=" in opening and "/twilio/silence?call_id=" in opening


def test_she_answers_what_they_say_and_reports_it(roster, talk, monkeypatch):
    call_id = a_call(roster, incident_id=make_incident(roster, type_="fire", severity="critical"))
    twilio_post(client, f"/twilio/voice?call_id={call_id}", {})
    monkeypatch.setattr(call_agent, "_llm_turn",
                        lambda conv: call_agent.Turn("Okay. Can you get to the shop floor in two minutes?", "none"))
    reply = say_(call_id, "I'm in the canteen")
    assert "Can you get to the shop floor" in reply and "<Gather" in reply  # she keeps listening
    assert talk[-1]["kind"] == "call_reply" and talk[-1]["text"] == "I'm in the canteen"
    assert call_agent.get(call_id).history[-2:] == [
        {"role": "user", "content": "I'm in the canteen"},
        {"role": "assistant", "content": "Okay. Can you get to the shop floor in two minutes?"}]
    roster.expire_all()
    assert roster.query(AuditLog).filter_by(action="call_reply").count() == 1


def test_going_acknowledges_and_ends_the_call(roster, talk):
    incident_id = make_incident(roster, type_="fire", severity="critical")
    call_id = a_call(roster, incident_id=incident_id)
    twilio_post(client, f"/twilio/voice?call_id={call_id}", {})
    reply = say_(call_id, "okay I'm going there now")
    assert "on your way" in reply and "<Hangup/>" in reply
    roster.expire_all()
    incident = roster.get(Incident, incident_id)
    assert incident.status == "acknowledged" and incident.acknowledged_by == "Ravi Kumar"


def test_asking_for_the_admin_puts_the_call_through(roster, talk):
    call_id = a_call(roster, incident_id=make_incident(roster, type_="fire", severity="critical"))
    twilio_post(client, f"/twilio/voice?call_id={call_id}", {})
    reply = say_(call_id, "can you connect me to the admin")
    admin = roster.query(Responder).filter_by(name="Anita Rao").one()
    assert "<Dial" in reply and admin.phone in reply and "connecting you to Anita Rao" in reply
    assert talk[-1]["connected_to"] == "Anita Rao"


def test_a_critical_fire_cannot_be_closed_from_the_phone(roster, talk, monkeypatch):
    incident_id = make_incident(roster, type_="fire", severity="critical")
    call_id = a_call(roster, incident_id=incident_id)
    twilio_post(client, f"/twilio/voice?call_id={call_id}", {})
    monkeypatch.setattr(call_agent, "_llm_turn", lambda conv: call_agent.Turn("Marked as false alarm!", "false_alarm"))
    reply = say_(call_id, "it's a false alarm")
    assert "needs the dashboard" in reply and "Marked as false alarm" not in reply
    roster.expire_all()
    assert roster.get(Incident, incident_id).status == "open"


def test_alerting_everyone_needs_a_yes_first(roster, talk, simulate):
    call_id = a_call(roster, incident_id=make_incident(roster, type_="fire", severity="critical"))
    twilio_post(client, f"/twilio/voice?call_id={call_id}", {})
    ask = say_(call_id, "alert everybody")
    assert "Just say yes" in ask and roster.query(RollCall).count() == 0
    done = say_(call_id, "yes")
    assert "alerting 4 guards" in done
    roster.expire_all()
    assert "Fire" in roster.query(RollCall).one().message  # the guards are told the problem


def test_keypad_still_works(roster, talk):
    incident_id = make_incident(roster, type_="unattended", severity="high")
    call_id = a_call(roster, incident_id=incident_id)
    twilio_post(client, f"/twilio/voice?call_id={call_id}", {})
    assert "false alarm" in say_(call_id, digits="2")
    roster.expire_all()
    assert roster.get(Incident, incident_id).status == "false_alarm"


def test_silence_asks_once_then_says_goodbye(roster, talk):
    call_id = a_call(roster, incident_id=make_incident(roster))
    twilio_post(client, f"/twilio/voice?call_id={call_id}", {})
    first = twilio_post(client, f"/twilio/silence?call_id={call_id}", {}).text
    assert "are you there?" in first and "<Gather" in first
    second = twilio_post(client, f"/twilio/silence?call_id={call_id}", {}).text
    assert "Goodbye" in second and "<Hangup/>" in second


def test_a_slow_answer_plays_a_filler_then_continues(roster, talk, monkeypatch):
    call_id = a_call(roster, incident_id=make_incident(roster))
    twilio_post(client, f"/twilio/voice?call_id={call_id}", {})
    monkeypatch.setattr(twilio_router, "TALK_WAIT_S", 0)
    first = say_(call_id, "where is it exactly")
    assert "one second" in first.lower() or "one moment" in first.lower() or "give me a second" in first.lower()
    assert "/twilio/next?call_id=" in first
    nxt = twilio_post(client, f"/twilio/next?call_id={call_id}", {}).text
    assert "<Gather" in nxt


def test_a_direct_call_reports_the_reply(roster, talk):
    call_id = a_call(roster, kind="direct", message="Yash in the control room needs you.")
    opening = twilio_post(client, f"/twilio/voice?call_id={call_id}", {}).text
    assert "Yash in the control room needs you." in opening and "connect you to the admin" in opening
    reply = say_(call_id, "I am at gate two")
    assert "pass that on" in reply and talk[-1]["text"] == "I am at gate two"


def test_calls_speak_in_the_rumik_voice_made_once(roster, talk, monkeypatch, tmp_path):
    made = []

    def speak(text):
        made.append(text)
        return b"RIFF-wav", 800

    s = get_settings()
    monkeypatch.setattr(s, "rumik_api_key", "k")
    monkeypatch.setattr(s, "rumik_gateway_url", "https://rumik.example")
    monkeypatch.setattr(s, "media_dir", str(tmp_path))
    monkeypatch.setattr(tts, "speak", speak)
    call_id = a_call(roster, incident_id=make_incident(roster, type_="fire", severity="critical"))
    first = twilio_post(client, f"/twilio/voice?call_id={call_id}", {}).text
    assert "<Play>" in first and "<Say" not in first and "/share/media/tts_" in first
    call_agent._calls.clear()
    twilio_post(client, f"/twilio/voice?call_id={call_id}", {})
    assert len(made) == 1  # the same sentence is made once and reused


def test_without_an_llm_she_never_says_the_same_thing_twice_in_a_row(roster, talk):
    call_id = a_call(roster, incident_id=make_incident(roster, type_="fire", severity="critical"))
    twilio_post(client, f"/twilio/voice?call_id={call_id}", {})
    replies = [say_(call_id, words) for words in ("hmm", "what", "where is it", "tell me more")]
    spoken = [r.split("<Say", 1)[1].split(">", 1)[1].split("</Say>", 1)[0] for r in replies]
    assert all(a != b for a, b in zip(spoken, spoken[1:]))
