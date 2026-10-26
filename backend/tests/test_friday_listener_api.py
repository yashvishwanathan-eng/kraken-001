"""Internal endpoints used by the wake-word listener (X-Internal-Key; acts as FRIDAY_USER)."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.config import get_settings
from app.main import app
from app.models import User
from app.routers import friday as friday_router
from app.ws import manager
from tests.conftest import check_in

client = TestClient(app)
AUDIO = {"audio": ("speech.wav", b"RIFF....WAVEfake", "audio/wav")}


@pytest.fixture
def key(monkeypatch):
    monkeypatch.setattr(get_settings(), "internal_key", "test-key")
    monkeypatch.setattr(get_settings(), "friday_user", "Anita Rao")
    return {"X-Internal-Key": "test-key"}


@pytest.fixture
def sent(monkeypatch):
    out = []

    async def fake_broadcast(message):
        out.append(message)
        return 1

    monkeypatch.setattr(manager, "broadcast", fake_broadcast)
    monkeypatch.setattr(manager, "publish_from_thread", out.append)
    return out


def anita_id(db):
    return db.scalar(select(User.id).where(User.name == "Anita Rao"))


def test_wake_event_says_whether_the_software_is_open(roster, key, sent, monkeypatch):
    monkeypatch.setattr(manager, "online_user_ids", lambda: set())
    out = client.post("/internal/friday/event", json={"state": "listening"}, headers=key).json()
    assert out == {"dashboards_open": 0, "friday_user_online": False}
    assert sent[-1] == {
        "kind": "friday",
        "state": "listening",
        "heard": None,
        "answer": None,
        "speak_for_user_id": None,
        "camera_ids": None,
        "open_path": None,
    }
    monkeypatch.setattr(manager, "online_user_ids", lambda: {anita_id(roster)})
    assert client.post("/internal/friday/event", json={"state": "listening"}, headers=key).json()["friday_user_online"]


def test_listener_voice_command_acts_as_friday_user(roster, key, sent, monkeypatch):
    monkeypatch.setattr(friday_router, "transcribe", lambda data, name, ctype: ("who is on duty", "groq"))
    monkeypatch.setattr(manager, "online_user_ids", lambda: {anita_id(roster)})
    check_in(roster, "Ravi Kumar")
    out = client.post("/internal/friday/voice-command", files=AUDIO, headers=key)
    assert out.status_code == 200, out.text
    body = out.json()
    assert (body["transcript"], body["intent"], body["spoken_by"]) == ("who is on duty", "on_duty", "dashboard")
    speaking = [m for m in sent if m.get("kind") == "friday" and m["state"] == "speaking"][-1]
    assert speaking["answer"].startswith("1 on duty") and speaking["speak_for_user_id"] == anita_id(roster)


def test_show_cameras_by_voice_sends_the_cameras_to_the_dashboard(roster, key, sent, monkeypatch):
    monkeypatch.setattr(friday_router, "transcribe", lambda data, name, ctype: ("show cameras 1 and 2", "groq"))
    out = client.post("/internal/friday/voice-command", files=AUDIO, headers=key).json()
    assert out["intent"] == "show_camera"
    speaking = [m for m in sent if m.get("kind") == "friday" and m["state"] == "speaking"][-1]
    assert speaking["camera_ids"] and len(speaking["camera_ids"]) == 2


def test_a_pretend_call_says_so(roster, key, sent, monkeypatch, simulate):
    monkeypatch.setattr(friday_router, "transcribe", lambda data, name, ctype: ("call ravi kumar", "groq"))
    out = client.post("/internal/friday/voice-command", files=AUDIO, headers=key).json()
    assert out["intent"] == "call_person" and "pretend call" in out["answer"]


def test_listener_speaks_itself_when_nobody_has_the_software_open(roster, key, sent, monkeypatch):
    monkeypatch.setattr(friday_router, "transcribe", lambda data, name, ctype: ("status report", "groq"))
    monkeypatch.setattr(manager, "online_user_ids", lambda: set())
    body = client.post("/internal/friday/voice-command", files=AUDIO, headers=key).json()
    assert body["spoken_by"] == "listener"


def test_internal_friday_needs_key_and_a_valid_user(roster, key, monkeypatch):
    assert client.post("/internal/friday/event", json={"state": "listening"}).status_code == 401
    monkeypatch.setattr(get_settings(), "friday_user", "Nobody Here")
    assert client.post("/internal/friday/voice-command", files=AUDIO, headers=key).status_code == 503


def test_spoken_yes_confirms_and_anything_else_cancels(roster, key, sent, monkeypatch):
    from app.services import commands

    calls = []
    monkeypatch.setattr(
        commands,
        "confirm",
        lambda token, yes, user: (
            calls.append((token, yes))
            or commands.CommandResult("evacuate", "done", "Evacuating." if yes else "Cancelled. Nothing was done.")
        ),
    )
    monkeypatch.setattr(manager, "online_user_ids", lambda: set())
    for said, expected in [("Yes, do it.", True), ("umm what", False), ("no", False)]:
        monkeypatch.setattr(friday_router, "transcribe", lambda data, name, ctype, said=said: (said, "groq"))
        body = client.post(
            "/internal/friday/voice-command", files=AUDIO, data={"confirm_token": "tok"}, headers=key
        ).json()
        assert calls[-1] == ("tok", expected)
        assert body["answer"] == ("Evacuating." if expected else "Cancelled. Nothing was done.")


def test_show_the_recording_by_voice_opens_the_incident(roster, key, sent, monkeypatch):
    from tests.conftest import make_incident

    incident_id = make_incident(roster, type_="fire", severity="critical")
    monkeypatch.setattr(friday_router, "transcribe", lambda data, name, ctype: ("show me the recording of the fire", "groq"))
    out = client.post("/internal/friday/voice-command", files=AUDIO, headers=key).json()
    assert out["intent"] == "show_recording" and f"incident {incident_id}" in out["answer"]
    speaking = [m for m in sent if m.get("kind") == "friday" and m["state"] == "speaking"][-1]
    assert speaking["open_path"] == f"/incidents/{incident_id}"
