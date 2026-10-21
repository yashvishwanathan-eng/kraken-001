"""WhatsApp messages (alert text + snapshot link).

PORT FROM BHOOMI: the request format below is Meta's WhatsApp Cloud API
(POST {WHATSAPP_API_URL} with "Authorization: Bearer {WHATSAPP_TOKEN}", where the URL looks like
https://graph.facebook.com/v20.0/<PHONE_NUMBER_ID>/messages). If Bhoomi uses a different provider,
replace only `_payload()` and the URL/header lines in `_post()` with Bhoomi's; everything else stays.

Note: WhatsApp business APIs usually allow free text only after the person has written to you in the
last 24 hours; otherwise a pre-approved template is needed. Test with the demo phones before the event.

Modes (WHATSAPP_MODE in .env): auto (live if URL + token are set, else off), live, simulate, off.
Every send has a 5 s timeout and one retry. A failed message is logged, never raised.
"""

import logging
from concurrent.futures import ThreadPoolExecutor

import httpx

from app.config import Settings, get_settings

log = logging.getLogger("safetyai.whatsapp")

TIMEOUT_S = 5.0
ATTEMPTS = 2
PARALLEL = 8  # alert-everybody sends to many phones at once


class WhatsAppService:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.sent: list[tuple[str, str, str | None]] = []  # (to, text, image) in simulate mode, for tests

    @property
    def mode(self) -> str:
        mode = self.settings.whatsapp_mode
        if mode == "auto":
            return "live" if (self.settings.whatsapp_api_url and self.settings.whatsapp_token) else "off"
        return mode

    def _payload(self, to: str, text: str, image_url: str | None) -> dict:
        number = to.lstrip("+")
        if image_url:
            return {"messaging_product": "whatsapp", "to": number, "type": "image",
                    "image": {"link": image_url, "caption": text}}
        return {"messaging_product": "whatsapp", "to": number, "type": "text", "text": {"body": text}}

    def _post(self, payload: dict) -> None:
        headers = {"Authorization": f"Bearer {self.settings.whatsapp_token}"}
        response = httpx.post(self.settings.whatsapp_api_url, json=payload, headers=headers, timeout=TIMEOUT_S)
        response.raise_for_status()

    def send(self, to: str, text: str, image_url: str | None = None) -> bool:
        """Returns True if the message was sent (or simulated)."""
        mode = self.mode
        if mode == "off":
            log.info("whatsapp off: not messaging %s", to)
            return False
        if mode == "simulate":
            self.sent.append((to, text, image_url))
            log.info("whatsapp simulated to %s: %s", to, text)
            return True
        payload = self._payload(to, text, image_url)
        for attempt in range(1, ATTEMPTS + 1):
            try:
                self._post(payload)
                return True
            except httpx.HTTPError as exc:
                log.warning("whatsapp to %s attempt=%d failed: %s", to, attempt, exc)
        return False

    def send_many(self, messages: list[tuple[str, str, str | None]]) -> int:
        """Send in parallel (alert everybody should reach every phone in about 2 s). Returns how many sent."""
        if not messages:
            return 0
        with ThreadPoolExecutor(max_workers=PARALLEL) as pool:
            results = list(pool.map(lambda m: self.send(*m), messages))
        return sum(results)


whatsapp = WhatsAppService()
