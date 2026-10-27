"""tools/check_twilio: reports each missing piece for real calls, with a fake Twilio (nothing is dialled)."""

import httpx
from sqlalchemy import select

from app.config import get_settings
from app.models import Responder
from tools import check_twilio


class Reply:
    def __init__(self, status, body=None, text=""):
        self.status_code, self._body, self.text = status, body or {}, text

    def json(self):
        return self._body


def fake_twilio(monkeypatch, verified, ngrok_up=True):
    placed = []

    def get(url, params=None, **_):
        if url.endswith("/Accounts/AC123.json"):
            return Reply(200, {"status": "active", "type": "Trial"})
        if "IncomingPhoneNumbers" in url:
            return Reply(200, {"incoming_phone_numbers": [{"phone_number": params["PhoneNumber"]}]})
        if "OutgoingCallerIds" in url:
            return Reply(200, {"outgoing_caller_ids": [{"phone_number": p} for p in verified]})
        raise AssertionError(url)

    def post(url, data=None, **_):
        if "/twilio/status" in url:
            if not ngrok_up:
                raise httpx.ConnectError("down")
            return Reply(403, text='{"detail":"bad Twilio signature"}')
        placed.append(data)
        return Reply(201, {"sid": "CA1"})

    monkeypatch.setattr(check_twilio.httpx, "get", get)
    monkeypatch.setattr(check_twilio.httpx, "post", post)
    return placed


def setup(monkeypatch, roster):
    s = get_settings()
    for key, value in [("twilio_account_sid", "AC123"), ("twilio_auth_token", "tok"),
                       ("twilio_from_number", "+15550000000"), ("public_base_url", "https://x.ngrok.app"),
                       ("calls_mode", "live")]:
        monkeypatch.setattr(s, key, value)
    people = list(roster.scalars(select(Responder).where(Responder.active.is_(True))))
    monkeypatch.setattr(check_twilio, "call_list", lambda: people)
    return people


def test_all_verified_passes_and_places_a_test_call(monkeypatch, roster, capsys):
    people = setup(monkeypatch, roster)
    placed = fake_twilio(monkeypatch, [p.phone for p in people])
    monkeypatch.setattr("sys.argv", ["check_twilio", "--call", people[0].name.upper()])
    assert check_twilio.main() == 0
    out = capsys.readouterr().out
    assert "All good" in out and "reaches this API" in out
    assert placed[0]["To"] == people[0].phone and "<Say" in placed[0]["Twiml"]
    for p in people:
        assert p.phone not in out  # only the last 4 digits are shown


def test_unverified_phone_and_ngrok_down_are_reported(monkeypatch, roster, capsys):
    people = setup(monkeypatch, roster)
    fake_twilio(monkeypatch, [p.phone for p in people[1:]], ngrok_up=False)
    monkeypatch.setattr("sys.argv", ["check_twilio"])
    assert check_twilio.main() == 1
    out = capsys.readouterr().out
    assert f"{people[0].name} ...{people[0].phone[-4:]} is NOT verified" in out
    assert "start ngrok http 8000" in out


def test_pretend_calls_are_reported_as_a_problem(monkeypatch, roster, capsys):
    people = setup(monkeypatch, roster)
    fake_twilio(monkeypatch, [p.phone for p in people])
    monkeypatch.setattr(get_settings(), "calls_mode", "simulate")
    monkeypatch.setenv("CALLS_MODE", "simulate")  # a Windows variable that hides the .env value
    monkeypatch.setattr("sys.argv", ["check_twilio"])
    assert check_twilio.main() == 1
    out = capsys.readouterr().out
    assert "calls are not real: CALLS_MODE=simulate" in out and "Windows environment variable" in out


def test_fast_replies_speak_new_sentences_at_once(monkeypatch, tmp_path):
    from app.services import call_voice

    s = get_settings()
    monkeypatch.setattr(s, "call_reply_voice", "fast")
    monkeypatch.setattr(call_voice, "media_root", lambda: tmp_path)
    monkeypatch.setattr(call_voice, "audio_link", lambda text, style="calm": "https://x/tts.wav")
    new = call_voice.speech("Where exactly is the smoke?")
    assert new.startswith("<Say voice=") and "Polly.Kajal-Neural" in new  # no wait for Rumik
    (tmp_path / call_voice.file_name("Go, Ravi!")).write_bytes(b"wav")
    assert call_voice.speech("Go, Ravi!").startswith("<Play>")  # made in advance: still the Rumik voice


def test_rumik_audio_is_shrunk_to_phone_audio(tmp_path):
    """P44: Rumik's WAV becomes 8 kHz mono mu-law (what a phone line carries), several times smaller."""
    import subprocess

    from app.services import call_voice

    src = tmp_path / "rumik.wav"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
                    "-ar", "24000", "-ac", "1", "-c:a", "pcm_s16le", str(src)], check=True)
    small = call_voice.phone_wav(src.read_bytes())
    assert len(small) < len(src.read_bytes()) / 4
    out = tmp_path / "phone.wav"
    out.write_bytes(small)
    header = out.read_bytes()[:44]  # mu-law WAV: read the sample rate from the header
    assert header[:4] == b"RIFF" and int.from_bytes(header[24:28], "little") == 8000


def test_phone_audio_falls_back_to_the_original_without_ffmpeg(monkeypatch):
    from app.services import call_voice

    def missing(*a, **k):
        raise FileNotFoundError("ffmpeg")

    monkeypatch.setattr(call_voice.subprocess, "run", missing)
    assert call_voice.phone_wav(b"RIFF-original") == b"RIFF-original"
