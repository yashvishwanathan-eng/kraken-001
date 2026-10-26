"""Friday's (Rumik) voice inside phone calls.

Twilio can read text itself (<Say>, a robot voice) or play an audio file from a link (<Play>). For the
Rumik voice we ask Rumik once for each sentence, save the WAV in the media folder as tts_<hash>.wav, and
give Twilio a signed /share/media link to it (the same links WhatsApp snapshots use). The same sentence is
never made twice: fixed parts (the keypad menu, "Thank you") are made once and reused for every call.

If Rumik is not set up, slow or down, or there is no public address for Twilio to fetch the file from,
the call uses <Say> instead, so a call never goes silent. After a Rumik failure it is skipped for a minute.
"""

import hashlib
import logging
import threading
import time
from xml.sax.saxutils import escape, quoteattr

from app.config import get_settings, media_root
from app.services import tts
from app.services.permissions import share_url

log = logging.getLogger("safetyai.call_voice")

RETRY_AFTER_S = 60.0
_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()
_failed_at = float("-inf")


def file_name(text: str) -> str:
    s = get_settings()
    key = f"{s.rumik_model}|{s.rumik_speaker}|{s.rumik_description}|{text}"
    return f"tts_{hashlib.sha1(key.encode()).hexdigest()[:24]}.wav"


def enabled() -> bool:
    s = get_settings()
    return (s.call_voice == "rumik" and bool(s.rumik_api_key and s.rumik_gateway_url)
            and s.public_base_url.startswith(("https://", "http://")))


def audio_link(text: str) -> str | None:
    """Signed public link to this sentence in the Rumik voice (made now if needed), or None -> use <Say>."""
    global _failed_at
    if not text or not enabled() or time.monotonic() - _failed_at < RETRY_AFTER_S:
        return None
    name = file_name(text)
    path = media_root() / name
    with _locks_guard:
        lock = _locks.setdefault(name, threading.Lock())
    with lock:  # two webhooks asking for the same sentence: Rumik is asked once
        if not path.exists():
            try:
                audio, ms = tts.speak(text)
            except tts.TtsUnavailable as exc:
                _failed_at = time.monotonic()
                log.warning("call voice: Rumik failed (%s); Twilio's own voice for the next minute", exc)
                return None
            path.write_bytes(audio)
            log.info("call voice: made %s in %d ms (%d chars)", name, ms, len(text))
    return share_url(name)


def prepare(text: str) -> None:
    """Make the audio in the background while the phone is still ringing, so nobody waits after picking up."""
    if enabled():
        threading.Thread(target=audio_link, args=(text,), name="call-voice", daemon=True).start()


def speech(text: str) -> str:
    """TwiML that speaks this text: Rumik when possible, else Twilio's <Say>."""
    link = audio_link(text)
    if link:
        return f"<Play>{escape(link)}</Play>"
    return f"<Say language={quoteattr(get_settings().call_language)}>{escape(text)}</Say>"
