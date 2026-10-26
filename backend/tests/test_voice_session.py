"""The listener's turn after "Hey Friday": record until quiet, send, answer, open the software if it is closed."""

import io
import wave

import numpy as np

from voice.session import Session, record_until_silence

RATE, CHUNK = 16000, 1280  # 80 ms chunks, like the microphone


def chunks(*parts):
    """parts: (seconds, amplitude) -> 80 ms int16 chunks of a 300 Hz tone (amplitude 0 = silence)."""
    for seconds, amp in parts:
        for _ in range(int(seconds * RATE / CHUNK)):
            t = np.arange(CHUNK) / RATE
            yield (amp * np.sin(2 * np.pi * 300 * t)).astype(np.int16)


def duration(wav_bytes):
    with wave.open(io.BytesIO(wav_bytes)) as w:
        return w.getnframes() / w.getframerate()


KW = dict(sample_rate=RATE, silence_s=1.0, max_s=12.0, start_timeout_s=5.0, rms_threshold=500)


def test_record_stops_after_one_second_of_silence():
    wav = record_until_silence(chunks((0.4, 0), (1.5, 8000), (3.0, 0)), **KW)
    assert abs(duration(wav) - 2.9) < 0.2  # 0.4 quiet lead-in + 1.5 speech + 1.0 quiet tail (then it stops)


def test_record_stops_at_max_length():
    wav = record_until_silence(chunks((20.0, 8000)), **KW)
    assert abs(duration(wav) - 12.0) < 0.2


def test_nothing_said_gives_nothing():
    assert record_until_silence(chunks((8.0, 0)), **KW) == b""


class FakeClient:
    def __init__(self, replies, online=True):
        self.replies, self.online = list(replies), online
        self.sent, self.events, self.confirmed = [], [], []

    def event(self, state):
        self.events.append(state)
        return {"dashboards_open": int(self.online), "friday_user_online": self.online}

    def voice_command(self, wav, confirm_token=None):
        self.sent.append(wav)
        if confirm_token:
            self.confirmed.append((confirm_token, wav))
            return {"status": "done", "answer": "Done.", "spoken_by": "listener"}
        return self.replies.pop(0)

    def speak(self, text):
        return b"RIFF-speech:" + text.encode()


def run(client, *recordings):
    played, opened = [], []
    session = Session(client, play=played.append, open_browser=opened.append, dashboard_url="http://localhost:5173")
    rec = iter(recordings)
    result = session.turn(lambda: next(rec))
    return result, played, opened


def test_closed_software_opens_friday_page_and_listener_speaks():
    client = FakeClient(
        [{"transcript": "status report", "status": "done", "answer": "All fine.", "spoken_by": "listener"}],
        online=False,
    )
    result, played, opened = run(client, b"RIFF-order")
    assert opened == ["http://localhost:5173/assistant"]
    assert played == [b"RIFF-speech:All fine."]
    assert result["answer"] == "All fine." and client.events[0] == "listening"


def test_open_software_speaks_on_the_dashboard_not_twice():
    client = FakeClient(
        [{"transcript": "status report", "status": "done", "answer": "All fine.", "spoken_by": "dashboard"}]
    )
    _, played, opened = run(client, b"RIFF-order")
    assert opened == [] and played == []


def test_silence_goes_back_to_waiting():
    client = FakeClient([])
    result, played, _ = run(client, b"")
    assert result is None and client.sent == [] and client.events[-1] == "idle"


def test_big_action_sends_the_spoken_answer_with_the_confirmation():
    client = FakeClient(
        [
            {
                "transcript": "evacuate",
                "status": "needs_confirmation",
                "confirm_token": "tok",
                "answer": "Say yes to confirm.",
                "spoken_by": "listener",
            },
        ]
    )
    result, played, _ = run(client, b"RIFF-evacuate", b"RIFF-yes")
    assert client.confirmed == [("tok", b"RIFF-yes")]  # the server hears "yes" and confirms
    assert played == [b"RIFF-speech:Say yes to confirm.", b"RIFF-speech:Done."]


def test_no_answer_to_a_big_action_cancels_it():
    client = FakeClient(
        [
            {
                "transcript": "evacuate",
                "status": "needs_confirmation",
                "confirm_token": "tok",
                "answer": "Say yes to confirm.",
                "spoken_by": "listener",
            },
        ]
    )
    result, played, _ = run(client, b"RIFF-evacuate", b"")
    assert client.confirmed == [] and played[-1] == b"RIFF-speech:Cancelled. Nothing was done."
