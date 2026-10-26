"""Text to speech with Rumik Silk: the assistant's spoken replies.

    POST {RUMIK_GATEWAY_URL}/v1/tts   Authorization: Bearer {RUMIK_API_KEY}
    body {"text", "model", "speaker"?}  ->  a 24 kHz WAV file

This is the HTTP form used by Rumik's own Pipecat package (pipecat-rumik). Only the reply text leaves the
laptop, never video. 8 s timeout and one retry; on failure the caller gets TtsUnavailable and the
dashboard falls back to the browser's own voice.
"""

import logging
import time

import httpx

from app.config import get_settings

log = logging.getLogger("safetyai.tts")


class TtsUnavailable(Exception):
    pass


def configured() -> bool:
    s = get_settings()
    return bool(s.rumik_api_key and s.rumik_gateway_url)


def description_for(style: str) -> str:
    s = get_settings()
    return s.rumik_urgent_description if style == "urgent" and s.rumik_urgent_description else s.rumik_description


def speak(text: str, style: str = "calm") -> tuple[bytes, int]:
    """(wav bytes, ms). style "urgent" asks for the emergency voice (mulberry). Raises TtsUnavailable."""
    s = get_settings()
    if not configured():
        raise TtsUnavailable("set RUMIK_API_KEY and RUMIK_GATEWAY_URL in .env")
    body = {"text": text, "model": s.rumik_model}
    if s.rumik_speaker:
        body["speaker"] = s.rumik_speaker
    if description_for(style) and s.rumik_model == "mulberry":
        body["description"] = description_for(style)  # Friday's voice described in words, calm or urgent
    url = f"{s.rumik_gateway_url.rstrip('/')}/v1/tts"
    headers = {"Authorization": f"Bearer {s.rumik_api_key}", "Content-Type": "application/json"}
    t0 = time.perf_counter()
    last_error = ""
    for attempt in (1, 2):
        try:
            response = httpx.post(url, json=body, headers=headers, timeout=s.rumik_timeout_s)
        except httpx.HTTPError as exc:
            last_error = f"no answer from Rumik ({type(exc).__name__})"
            continue
        if response.status_code == 200 and response.content[:4] == b"RIFF":
            ms = int((time.perf_counter() - t0) * 1000)
            log.info("step=tts chars=%d ms=%d attempt=%d", len(text), ms, attempt)
            return response.content, ms
        if response.status_code in (401, 403):
            raise TtsUnavailable(f"Rumik refused the key (HTTP {response.status_code}); check RUMIK_API_KEY")
        last_error = f"Rumik answered HTTP {response.status_code}: {response.text[:200]}"
    log.warning("step=tts failed: %s", last_error)
    raise TtsUnavailable(last_error)
