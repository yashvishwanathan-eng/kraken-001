"""One conversation turn after "Hey Friday", without any real audio (so it is unit-tested).

record_until_silence: keeps the microphone chunks from when you start talking until about 1 s of quiet
(or 12 s at most) and returns them as a WAV. Nothing said within 5 s: returns b"" and Friday goes back to waiting.

Session.turn:
  1. tells the API "listening" (every open dashboard shows Friday's page); if the listener's user does not
     have the software open, it opens the browser on Friday's page
  2. records your order and sends it (/internal/friday/voice-command)
  3. plays the answer on the laptop speaker only when no dashboard of that user is open (else the dashboard
     speaks it, so the blob moves with her voice and it is never heard twice)
  4. a big action (alert everyone, evacuate, fire station) waits for a spoken "yes"; anything else cancels it
"""

import io
import logging
import wave
from collections.abc import Callable, Iterable

import numpy as np

log = logging.getLogger("safetyai.voice")

def record_until_silence(chunks: Iterable[np.ndarray], *, sample_rate: int, silence_s: float, max_s: float,
                         start_timeout_s: float, rms_threshold: int) -> bytes:
    kept: list[np.ndarray] = []
    heard = False
    quiet_s = 0.0
    total_s = 0.0
    for chunk in chunks:
        seconds = len(chunk) / sample_rate
        total_s += seconds
        loud = float(np.sqrt(np.mean(chunk.astype(np.float64) ** 2))) >= rms_threshold
        kept.append(chunk)
        if loud:
            heard, quiet_s = True, 0.0
        elif heard:
            quiet_s += seconds
            if quiet_s >= silence_s:
                break
        elif total_s >= start_timeout_s:
            return b""
        if total_s >= max_s:
            break
    if not heard:
        return b""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(np.concatenate(kept).astype(np.int16).tobytes())
    return buffer.getvalue()


class Session:
    def __init__(self, client, play: Callable[[bytes], None], open_browser: Callable[[str], None],
                 dashboard_url: str) -> None:
        self.client = client
        self.play = play
        self.open_browser = open_browser
        self.dashboard_url = dashboard_url.rstrip("/")

    def _answer(self, reply: dict) -> None:
        if reply.get("spoken_by") == "listener" and reply.get("answer"):
            self.play(self.client.speak(reply["answer"]))

    def turn(self, record: Callable[[], bytes]) -> dict | None:
        status = self.client.event("listening")
        if not status.get("friday_user_online"):
            self.open_browser(f"{self.dashboard_url}/assistant")
        wav = record()
        if not wav:
            log.info("nothing said: back to waiting")
            self.client.event("idle")
            return None
        reply = self.client.voice_command(wav)
        log.info("heard %r -> %s: %s", reply.get("transcript"), reply.get("status"), reply.get("answer"))
        self._answer(reply)
        if reply.get("status") == "needs_confirmation" and reply.get("confirm_token"):
            said = record()  # "yes" or "no": the server hears it and confirms or cancels (unclear = no)
            if not said:
                self._answer({"answer": "Cancelled. Nothing was done.", "spoken_by": reply.get("spoken_by")})
                return reply
            result = self.client.voice_command(said, confirm_token=reply["confirm_token"])
            self._answer(result)
            return result
        return reply
