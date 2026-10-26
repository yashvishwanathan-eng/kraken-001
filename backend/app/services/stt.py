"""Speech to text: Groq Whisper online, faster-whisper ("small") on the laptop offline.

Groq needs GROQ_API_KEY + GROQ_STT_MODEL (e.g. whisper-large-v3-turbo) and OFFLINE_MODE=false.
faster-whisper downloads its model once on first use (about 500 MB for "small"), then works offline.
"""

import logging
import tempfile
import threading
import time
from pathlib import Path

import httpx

from app.config import get_settings

log = logging.getLogger("safetyai.stt")

GROQ_STT_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
GROQ_STT_TIMEOUT_S = 10.0

_local_model = None
_local_lock = threading.Lock()


class SttUnavailable(Exception):
    pass


def _groq_fields(s) -> dict[str, str]:
    fields = {"model": s.groq_stt_model, "response_format": "json"}
    if s.stt_language:
        fields["language"] = s.stt_language
    if s.stt_prompt:
        fields["prompt"] = s.stt_prompt
    return fields


def _groq(data: bytes, filename: str, content_type: str) -> str:
    s = get_settings()
    response = httpx.post(GROQ_STT_URL, headers={"Authorization": f"Bearer {s.groq_api_key}"},
                          files={"file": (filename, data, content_type)},
                          data=_groq_fields(s), timeout=GROQ_STT_TIMEOUT_S)
    response.raise_for_status()
    return response.json()["text"].strip()


def _local(data: bytes, filename: str) -> str:
    global _local_model
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise SttUnavailable("faster-whisper is not installed (pip install -r requirements.txt)") from exc
    with _local_lock:
        if _local_model is None:
            log.info("loading faster-whisper %s (first use downloads it)", get_settings().whisper_offline_model)
            _local_model = WhisperModel(get_settings().whisper_offline_model, device="cpu", compute_type="int8")
        suffix = Path(filename).suffix or ".wav"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(data)
            path = tmp.name
        try:
            s = get_settings()
            segments, _ = _local_model.transcribe(path, vad_filter=True, language=s.stt_language or None,
                                                  initial_prompt=s.stt_prompt or None)
            return " ".join(segment.text.strip() for segment in segments).strip()
        finally:
            Path(path).unlink(missing_ok=True)


def transcribe(data: bytes, filename: str, content_type: str) -> tuple[str, str]:
    """Returns (text, source) with source 'groq' or 'local'."""
    s = get_settings()
    t0 = time.perf_counter()
    if s.groq_api_key and s.groq_stt_model and not s.offline_mode:
        try:
            text = _groq(data, filename, content_type)
            log.info("step=stt source=groq ms=%.0f", (time.perf_counter() - t0) * 1000)
            return text, "groq"
        except httpx.HTTPError as exc:
            log.warning("groq speech-to-text failed, trying local: %s", exc)
    try:
        text = _local(data, filename)
    except SttUnavailable:
        raise
    except Exception as exc:
        raise SttUnavailable(f"local speech-to-text failed: {exc}") from exc
    log.info("step=stt source=local ms=%.0f", (time.perf_counter() - t0) * 1000)
    return text, "local"
