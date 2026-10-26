"""The Conversations history (P31): laptop talks with the voice clip, the API, and silence handling."""

from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app
from app.models import Conversation
from app.routers import assistant as assistant_router
from app.services import stt
from tests.conftest import login

client = TestClient(app)
AUDIO = {"audio": ("speech.webm", b"\x1aE\xdf\xa3fake", "audio/webm")}


def test_laptop_talks_are_kept_with_the_voice(roster, monkeypatch, tmp_path):
    monkeypatch.setattr(get_settings(), "media_dir", str(tmp_path))
    monkeypatch.setattr(assistant_router, "transcribe", lambda data, name, ctype: ("who is on duty", "groq"))
    headers = login(client, "1111")
    client.post("/assistant/voice-command", files=AUDIO, headers=headers)
    client.post("/assistant/command", json={"text": "status report"}, headers=headers)
    roster.expire_all()
    conv = roster.query(Conversation).filter_by(channel="laptop").one()  # one session while they keep talking
    said = [(t.speaker, t.text, t.action) for t in conv.turns]
    assert said[0][:2] == ("person", "who is on duty") and said[0][2] == "voice"
    assert said[1][0] == "friday" and said[2] == ("person", "status report", "typed")
    clip = conv.turns[0].audio_path
    assert clip.startswith("voice/") and (tmp_path / clip).read_bytes().startswith(b"\x1aE")

    listed = client.get("/conversations?channel=laptop", headers=headers).json()
    assert listed[0]["id"] == conv.id and listed[0]["turns"] == 4
    one = client.get(f"/conversations/{conv.id}", headers=headers).json()
    assert one["lines"][0]["audio_url"] == f"/media/{clip}" and one["lines"][1]["speaker"] == "friday"


def test_only_admin_and_manager_see_conversations(roster):
    assert client.get("/conversations", headers=login(client, "3333")).status_code == 403  # Head Guard
    assert client.get("/conversations", headers=login(client, "2222")).status_code == 200  # Manager
    assert client.get("/conversations?channel=fax", headers=login(client, "1111")).status_code == 422


def test_silence_is_just_sorry_i_didnt_catch_that(roster, monkeypatch, tmp_path):
    monkeypatch.setattr(get_settings(), "media_dir", str(tmp_path))
    monkeypatch.setattr(assistant_router, "transcribe", lambda data, name, ctype: ("", "groq"))
    out = client.post("/assistant/voice-command", files=AUDIO, headers=login(client, "1111")).json()
    assert out["answer"] == "Sorry, I didn't catch that."


def test_whisper_inventions_in_silence_count_as_nothing():
    for heard in ("Okay, bye.", "Thank you.", "Thanks for watching!", " you ", "Friday, RingGuard. Status report."):
        assert stt.cleaned(heard) == "", heard
    assert stt.cleaned("Okay, show camera 2") == "Okay, show camera 2"
