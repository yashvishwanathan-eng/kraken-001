"""Phone calls through Twilio.

How a call works:
  1. We ask Twilio's REST API to call a number. We give it two URLs on our server:
     /twilio/voice  (what to say when they pick up)  and  /twilio/status  (ringing, no answer, ...).
  2. When the guard answers, Twilio fetches /twilio/voice and reads our TwiML out loud.
  3. Keypresses and speech come back to /twilio/gather.

Calls go through one queue with a single dispatcher thread, so we never exceed Twilio's rate
(about 1 new call per second by default). Nobody is dropped; later calls just wait their turn.

Modes (CALLS_MODE in .env):
  auto      live if TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_FROM_NUMBER and PUBLIC_BASE_URL are set, else off
  live      really call
  simulate  pretend: rows and timeline entries are written, nothing is dialled (for demos and tests)
  off       no calls at all (WhatsApp and the dashboard still alert)
"""

import logging
import queue
import threading
import time
from collections.abc import Callable
from xml.sax.saxutils import escape, quoteattr

import httpx
from sqlalchemy import select

from app.config import PRODUCT_NAME, Settings, get_settings
from app.db import SessionLocal
from app.models import Call
from app.services import incidents as incident_ops
from app.ws import manager

log = logging.getLogger("safetyai.calls")

TWILIO_API = "https://api.twilio.com/2010-04-01"
HTTP_TIMEOUT_S = 5.0
ATTEMPTS = 2  # one try + one retry
FINAL_STATUSES = {"completed", "no-answer", "busy", "failed", "canceled"}
NOT_REACHED = {"no-answer", "busy", "failed", "canceled"}


class TwilioError(Exception):
    pass


class TwilioRejected(TwilioError):
    """Twilio said no (bad number, number not verified on a trial account): retrying will not help."""


class TwilioClient:
    """The two Twilio REST calls we need, over plain HTTP (no SDK)."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def configured(self) -> bool:
        s = self.settings
        return bool(s.twilio_account_sid and s.twilio_auth_token and s.twilio_from_number and s.public_base_url)

    def _post(self, path: str, data: dict) -> dict:
        s = self.settings
        url = f"{TWILIO_API}/Accounts/{s.twilio_account_sid}{path}"
        last = "no attempt made"
        for attempt in range(1, ATTEMPTS + 1):
            try:
                response = httpx.post(url, data=data, auth=(s.twilio_account_sid, s.twilio_auth_token),
                                      timeout=HTTP_TIMEOUT_S)
            except httpx.TransportError as exc:  # network trouble: worth one retry
                last = str(exc)
                log.warning("twilio attempt=%d failed: %s", attempt, exc)
                continue
            if response.status_code >= 500:  # Twilio's side: worth one retry
                last = f"Twilio {response.status_code}"
                log.warning("twilio attempt=%d failed: %s", attempt, last)
                continue
            if response.status_code >= 400:
                raise TwilioRejected(f"Twilio {response.status_code}: {response.text[:300]}")
            return response.json()
        raise TwilioError(last)

    def public_ok(self) -> bool:
        """Twilio can reach our webhooks only through a full public address (ngrok: https://....ngrok-free.app)."""
        return self.settings.public_base_url.startswith(("https://", "http://"))

    def create_call(self, to: str, call_id: int, speak_only: str | None = None) -> str:
        if speak_only is not None:  # no public address: Twilio reads this text, no keypresses come back
            return self._post("/Calls.json", {"To": to, "From": self.settings.twilio_from_number,
                                              "Twiml": speak_only, "Timeout": str(self.settings.call_ring_timeout_s)})
        base = self.settings.public_base_url.rstrip("/")
        result = self._post("/Calls.json", {
            "To": to,
            "From": self.settings.twilio_from_number,
            "Url": f"{base}/twilio/voice?call_id={call_id}",
            "Method": "POST",
            "StatusCallback": f"{base}/twilio/status?call_id={call_id}",
            "StatusCallbackMethod": "POST",
            "StatusCallbackEvent": ["initiated", "ringing", "answered", "completed"],
            "Timeout": str(self.settings.call_ring_timeout_s),
        })
        return result["sid"]

    def hangup(self, call_sid: str) -> None:
        self._post(f"/Calls/{call_sid}.json", {"Status": "completed"})


class CallService:
    def __init__(self, settings: Settings | None = None, client: TwilioClient | None = None,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self.settings = settings or get_settings()
        self.client = client or TwilioClient(self.settings)
        self._sleep = sleep
        self._queue: queue.Queue[int | None] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._last_dial = 0.0

    @property
    def mode(self) -> str:
        mode = self.settings.calls_mode
        if mode == "auto":
            return "live" if self.client.configured() else "off"
        return mode

    # ---------- lifecycle ----------

    def start(self) -> None:
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._dispatch_loop, name="call-dispatcher", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        if self._thread is not None:
            self._queue.put(None)
            self._thread.join(timeout=5)
            self._thread = None

    # ---------- placing calls ----------

    def place(self, *, kind: str, to_name: str, to_phone: str, responder_id: int | None = None,
              incident_id: int | None = None, roll_call_id: int | None = None, message: str | None = None,
              audio_file: str | None = None) -> int | None:
        """Create the call row and queue it. Returns the call id, or None when calls are off."""
        mode = self.mode
        if mode == "off":
            log.info("calls off: not calling %s (incident=%s kind=%s)", to_name, incident_id, kind)
            return None
        with SessionLocal() as db:
            call = Call(kind=kind, to_name=to_name, to_phone=to_phone, responder_id=responder_id,
                        incident_id=incident_id, roll_call_id=roll_call_id, message=message,
                        audio_file=audio_file, status="queued" if mode == "live" else "simulated")
            db.add(call)
            db.commit()
            call_id = call.id
        if mode == "live":
            self.start()
            self._queue.put(call_id)
        else:
            log.info("call=%d simulated: would call %s (%s) kind=%s", call_id, to_name, to_phone, kind)
            self._after_placed(call_id)
        return call_id

    def cancel_pending(self, incident_id: int | None = None) -> int:
        """Stop calls that have not been dialled yet (all, or one incident's). Returns how many."""
        with SessionLocal() as db:
            query = select(Call).where(Call.status == "queued")
            if incident_id is not None:
                query = query.where(Call.incident_id == incident_id)
            calls = list(db.scalars(query))
            for call in calls:
                call.status = "canceled"
            db.commit()
            return len(calls)

    def hang_up_active(self, incident_id: int | None = None, except_call_id: int | None = None) -> int:
        """Hang up calls that are ringing or connected (e.g. someone else already acknowledged)."""
        with SessionLocal() as db:
            query = select(Call).where(Call.call_sid.is_not(None), Call.status.not_in(FINAL_STATUSES))
            if incident_id is not None:
                query = query.where(Call.incident_id == incident_id)
            calls = [c for c in db.scalars(query) if c.id != except_call_id]
        hung = 0
        for call in calls:
            try:
                self.client.hangup(call.call_sid)
                hung += 1
            except TwilioError as exc:
                log.warning("call=%d hangup failed: %s", call.id, exc)
        return hung

    # ---------- dispatcher ----------

    def _dispatch_loop(self) -> None:
        while (call_id := self._queue.get()) is not None:
            try:
                self._dial(call_id)
            except Exception:
                log.exception("call=%d dispatch crashed", call_id)

    def _dial(self, call_id: int) -> None:
        with SessionLocal() as db:
            call = db.get(Call, call_id)
            if call is None or call.status != "queued":
                return  # cancelled while waiting
            to_phone = call.to_phone
        wait = self._last_dial + 1.0 / self.settings.twilio_cps - time.monotonic()
        if wait > 0:
            self._sleep(wait)  # stay under Twilio's calls-per-second limit
        self._last_dial = time.monotonic()
        t0 = time.perf_counter()
        speak_only = None if self.client.public_ok() else self._speak_only(call_id)
        try:
            sid = self.client.create_call(to_phone, call_id, speak_only)
        except TwilioError as exc:
            log.error("call=%d step=dial failed: %s (WhatsApp and the dashboard still alert)", call_id, exc)
            with SessionLocal() as db:
                call = db.get(Call, call_id)
                call.status = "failed"
                db.commit()
            return
        with SessionLocal() as db:
            call = db.get(Call, call_id)
            call.call_sid, call.status = sid, "initiated"
            db.commit()
        log.info("call=%d step=dial sid=%s ms=%.1f", call_id, sid, (time.perf_counter() - t0) * 1000)
        self._after_placed(call_id)

    def _speak_only(self, call_id: int) -> str:
        """The call still tells the problem (twice) when PUBLIC_BASE_URL is missing or wrong."""
        from app.services.escalation import alert_text  # escalation imports this module

        with SessionLocal() as db:
            call = db.get(Call, call_id)
            incident = incident_ops.load(db, call.incident_id) if call.kind == "alert" and call.incident_id else None
            text = alert_text(incident, self.settings.timezone) if incident else (call.message or "")
        if not text.startswith(PRODUCT_NAME):
            text = f"{PRODUCT_NAME} alert. {text}"
        text += f" Please open the {PRODUCT_NAME} dashboard to respond."
        log.warning("call=%d PUBLIC_BASE_URL is not a full https address: speak-only call, no keypresses", call_id)
        say = f"<Say language={quoteattr(self.settings.call_language)}>{escape(text)}</Say>"
        return f'<Response>{say}<Pause length="1"/>{say}</Response>'

    def _after_placed(self, call_id: int) -> None:
        """Alert calls go on the incident timeline as call_placed, and dashboards get the update."""
        with SessionLocal() as db:
            call = db.get(Call, call_id)
            if call is None or call.kind != "alert" or call.incident_id is None:
                return
            incident = incident_ops.load(db, call.incident_id)
            incident_ops.add_timeline(incident, "call_placed", to=call.to_name)
            db.commit()
            manager.publish_from_thread(incident_ops.to_message(incident))


calls = CallService()
