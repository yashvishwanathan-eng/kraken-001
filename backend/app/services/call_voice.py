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
import subprocess
import threading
import time
from xml.sax.saxutils import escape, quoteattr

from app.config import get_settings, media_root
from app.services import tts
from app.services.permissions import share_url

log = logging.getLogger("safetyai.call_voice")

RETRY_AFTER_S = 60.0
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)  # Windows: ffmpeg runs without a console window
READY_WAIT_S = 0.8  # longest a caller waits for a sentence that is still being made in the Rumik voice
_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()
_failed_at = float("-inf")


def file_name(text: str, style: str = "calm") -> str:
    s = get_settings()
    key = f"{s.rumik_model}|{s.rumik_speaker}|{tts.description_for(style)}|{text}"
    return f"tts_{hashlib.sha1(key.encode()).hexdigest()[:24]}.wav"


def enabled() -> bool:
    s = get_settings()
    return (s.call_voice == "rumik" and bool(s.rumik_api_key and s.rumik_gateway_url)
            and s.public_base_url.startswith(("https://", "http://")))


def phone_wav(audio: bytes) -> bytes:
    """P44: Rumik's WAV as phone audio (8 kHz mono mu-law), about 6x smaller, so Twilio fetches it through ngrok
    quickly and the caller hears no gap. A phone line carries 8 kHz anyway, so it sounds the same. If FFmpeg is
    missing or fails, the original WAV is kept."""
    try:
        done = subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", "pipe:0", "-ar", "8000", "-ac", "1",
             "-c:a", "pcm_mulaw", "-f", "wav", "pipe:1"],
            input=audio, capture_output=True, timeout=5, creationflags=NO_WINDOW)
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("call voice: could not shrink the audio for the phone (%s); sending it as made", exc)
        return audio
    if done.returncode != 0 or not done.stdout:
        log.warning("call voice: ffmpeg could not shrink the audio; sending it as made")
        return audio
    return done.stdout


def audio_link(text: str, style: str = "calm") -> str | None:
    """Signed public link to this sentence in the Rumik voice (made now if needed), or None -> use <Say>."""
    global _failed_at
    if not text or not enabled() or time.monotonic() - _failed_at < RETRY_AFTER_S:
        return None
    name = file_name(text, style)
    path = media_root() / name
    with _locks_guard:
        lock = _locks.setdefault(name, threading.Lock())
    with lock:  # two webhooks asking for the same sentence: Rumik is asked once
        if not path.exists():
            try:
                audio, ms = tts.speak(text, style)
            except tts.TtsUnavailable as exc:
                _failed_at = time.monotonic()
                log.warning("call voice: Rumik failed (%s); Twilio's own voice for the next minute", exc)
                return None
            small = phone_wav(audio)
            path.write_bytes(small)
            log.info("call voice: made %s in %d ms (%d chars, %d KB for the phone)", name, ms, len(text),
                     len(small) // 1024)
    return share_url(name)


def prepare(text: str, style: str = "calm") -> None:
    """Make the audio in the background while the phone is still ringing, so nobody waits after picking up."""
    if enabled():
        threading.Thread(target=audio_link, args=(text, style), name="call-voice", daemon=True).start()


def fast_replies() -> bool:
    return get_settings().call_reply_voice == "fast"


def speech(text: str, style: str = "calm") -> str:
    """TwiML that speaks this text at once: the Rumik file when it is ready, else Twilio's voice.

    Never makes the person on the phone wait for Rumik (P41): a sentence still being made (the opening is made
    while the phone rings) gets up to READY_WAIT_S; a sentence not made at all is spoken by Twilio's voice now
    and made in the background, so it is ready the next time it is needed."""
    s = get_settings()
    name = file_name(text, style)
    path = media_root() / name
    if not path.exists() and enabled() and time.monotonic() - _failed_at >= RETRY_AFTER_S:
        with _locks_guard:
            lock = _locks.get(name)
        if lock is not None and lock.acquire(timeout=READY_WAIT_S):  # being made right now: a short wait at most
            lock.release()
        if not path.exists():
            prepare(text, style)
    if path.exists():
        link = audio_link(text, style)
        if link:
            return f"<Play>{escape(link)}</Play>"
    if fast_replies() or enabled():  # Twilio's Indian English voice, closest to Friday's
        return f"<Say voice={quoteattr(s.call_say_voice)} language={quoteattr(s.call_language)}>{escape(text)}</Say>"
    return f"<Say language={quoteattr(s.call_language)}>{escape(text)}</Say>"
