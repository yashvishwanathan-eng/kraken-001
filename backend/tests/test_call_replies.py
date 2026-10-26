"""Friday on the phone: a short conversation (CALL_STYLE=conversation) instead of a keypad menu."""

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app
from app.models import Call, Conversation, Incident, Responder, RollCall
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


def lines(db, call_id):
    db.expire_all()
    conv = db.query(Conversation).filter_by(call_id=call_id).one()
    return [(t.speaker, t.text) for t in conv.turns]


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
    assert "Ravi, it&apos;s Friday from RingGuard" in opening.replace("'", "&apos;")
    assert "right now! Where are you?" in opening and "Press" not in opening and "alert." not in opening
    assert "/twilio/talk?call_id=" in opening and "/twilio/silence?call_id=" in opening


def test_she_answers_what_they_say_and_reports_it(roster, talk, monkeypatch):
    call_id = a_call(roster, incident_id=make_incident(roster, type_="fire", severity="critical"))
    twilio_post(client, f"/twilio/voice?call_id={call_id}", {})
    monkeypatch.setattr(call_agent, "_llm_turn",
                        lambda conv: call_agent.Turn("Okay. Can you get to the shop floor in two minutes?", "none"))
    reply = say_(call_id, "I'm in the canteen")
    assert "Can you get to the shop floor" in reply and "<Gather" in reply  # she keeps listening
    assert not [m for m in talk if m.get("kind") == "call_reply"]  # nothing goes to Friday's page any more
    assert call_agent.get(call_id).history[-2:] == [
        {"role": "user", "content": "I'm in the canteen"},
        {"role": "assistant", "content": "Okay. Can you get to the shop floor in two minutes?"}]
    assert lines(roster, call_id) == [
        ("friday", "Ravi, it's Friday from RingGuard. There's a fire at Laptop Webcam, near the server room door, "
                   "right now! Where are you?"),
        ("person", "I'm in the canteen"),
        ("friday", "Okay. Can you get to the shop floor in two minutes?")]


def test_going_acknowledges_and_ends_the_call(roster, talk):
    incident_id = make_incident(roster, type_="fire", severity="critical")
    call_id = a_call(roster, incident_id=incident_id)
    twilio_post(client, f"/twilio/voice?call_id={call_id}", {})
    reply = say_(call_id, "okay I'm going there now")
    assert "on the way" in reply and "<Hangup/>" in reply
    roster.expire_all()
    incident = roster.get(Incident, incident_id)
    assert incident.status == "acknowledged" and incident.acknowledged_by == "Ravi Kumar"


def test_asking_for_the_admin_puts_the_call_through(roster, talk):
    call_id = a_call(roster, incident_id=make_incident(roster, type_="fire", severity="critical"))
    twilio_post(client, f"/twilio/voice?call_id={call_id}", {})
    reply = say_(call_id, "can you connect me to the admin")
    admin = roster.query(Responder).filter_by(name="Anita Rao").one()
    assert "<Dial" in reply and admin.phone in reply and "connecting you to Anita Rao" in reply
    assert lines(roster, call_id)[-1] == ("friday", "Okay, connecting you to Anita Rao now.")


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
    assert "pass that on" in reply and ("person", "I am at gate two") in lines(roster, call_id)


def test_calls_speak_in_the_rumik_voice_made_once(roster, talk, monkeypatch, tmp_path):
    made = []

    def speak(text, style="calm"):
        made.append((text, style))
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
    assert len(made) == 1 and made[0][1] == "urgent"  # made once, reused; a fire call sounds urgent


def test_without_an_llm_she_never_says_the_same_thing_twice_in_a_row(roster, talk):
    call_id = a_call(roster, incident_id=make_incident(roster, type_="fire", severity="critical"))
    twilio_post(client, f"/twilio/voice?call_id={call_id}", {})
    replies = [say_(call_id, words) for words in ("hmm", "what", "where is it", "tell me more")]
    spoken = [r.split("<Say", 1)[1].split(">", 1)[1].split("</Say>", 1)[0] for r in replies]
    assert all(a != b for a, b in zip(spoken, spoken[1:]))


def test_im_heading_there_is_handled_at_once_without_the_llm(roster, talk, monkeypatch):
    incident_id = make_incident(roster, type_="fire", severity="critical")
    call_id = a_call(roster, incident_id=incident_id)
    twilio_post(client, f"/twilio/voice?call_id={call_id}", {})
    monkeypatch.setattr(call_agent, "_llm_turn", lambda conv: pytest.fail("no LLM for 'I'm heading there'"))
    reply = say_(call_id, "yes I'm heading right there")
    assert "Go, Ravi!" in reply and "<Hangup/>" in reply
    roster.expire_all()
    assert roster.get(Incident, incident_id).status == "acknowledged"


def test_not_going_is_not_taken_as_going(roster, talk):
    call_id = a_call(roster, incident_id=make_incident(roster, type_="fire", severity="critical"))
    twilio_post(client, f"/twilio/voice?call_id={call_id}", {})
    assert "Go, Ravi!" not in say_(call_id, "I can't go there right now")


def test_the_call_recording_is_kept_with_the_conversation(roster, talk, monkeypatch, tmp_path):
    from app.services import conversations

    monkeypatch.setattr(get_settings(), "media_dir", str(tmp_path))

    class Reply:
        content = b"ID3-mp3"

        def raise_for_status(self):
            pass

    monkeypatch.setattr(conversations.httpx, "get", lambda url, **kw: Reply() if url.endswith(".mp3") else None)
    monkeypatch.setattr(conversations, "save_call_recording", lambda cid, url: conversations._download(cid, url))
    call_id = a_call(roster, incident_id=make_incident(roster))
    twilio_post(client, f"/twilio/voice?call_id={call_id}", {})
    twilio_post(client, f"/twilio/recording?call_id={call_id}",
                {"RecordingStatus": "completed", "RecordingUrl": "https://api.twilio.com/rec/RE1"})
    roster.expire_all()
    conv = roster.query(Conversation).filter_by(call_id=call_id).one()
    assert conv.recording_path == f"calls/call_{call_id}.mp3"
    assert (tmp_path / "calls" / f"call_{call_id}.mp3").read_bytes() == b"ID3-mp3"
