"""POST /assistant/voice-command: speech in, Friday's command result out (what was heard + what was done)."""

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import app
from app.models import AuditLog
from app.routers import assistant as assistant_router
from tests.conftest import check_in, login

client = TestClient(app)
AUDIO = {"audio": ("speech.webm", b"\x1aE\xdf\xa3fake-webm", "audio/webm")}


def test_voice_command_runs_what_was_said(roster, monkeypatch):
    monkeypatch.setattr(assistant_router, "transcribe", lambda data, name, ctype: ("who is on duty", "groq"))
    check_in(roster, "Ravi Kumar")
    out = client.post("/assistant/voice-command", files=AUDIO, headers=login(client, "1111"))
    assert out.status_code == 200, out.text
    body = out.json()
    assert (body["transcript"], body["intent"], body["status"]) == ("who is on duty", "on_duty", "done")
    assert body["answer"].startswith("1 on duty: Ravi Kumar")
    row = roster.scalar(select(AuditLog).where(AuditLog.action == "assistant_command"))
    assert (row.channel, row.detail["text"]) == ("voice", "who is on duty")


def test_voice_command_with_no_speech_says_sorry(roster, monkeypatch):
    monkeypatch.setattr(assistant_router, "transcribe", lambda data, name, ctype: ("", "groq"))
    body = client.post("/assistant/voice-command", files=AUDIO, headers=login(client, "1111")).json()
    assert (body["status"], body["answer"]) == ("unknown", "Sorry, say that again.")


def test_voice_command_rejects_non_audio(roster):
    bad = {"audio": ("notes.txt", b"hello", "text/plain")}
    assert client.post("/assistant/voice-command", files=bad, headers=login(client, "1111")).status_code == 422
