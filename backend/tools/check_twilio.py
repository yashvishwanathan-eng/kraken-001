"""Check everything a real alert call needs, without showing any secret. Run on the laptop, from backend/:

    python -m tools.check_twilio                    account, caller number, verified phones, public address
    python -m tools.check_twilio --call yash        also ring one person on the call list with a short test message

Checks, in order:
  1. CALLS_MODE and which .env keys are set (values are never printed; phones show the last 4 digits only)
  2. the Twilio account answers (trial or full) and TWILIO_FROM_NUMBER belongs to it
  3. trial accounts can only call verified numbers: every phone on the call list is checked against them
  4. PUBLIC_BASE_URL (ngrok) reaches this API: Twilio needs it to read the alert and send keypresses back
The test call reads its message straight from the request, so it works even before ngrok is running.
"""

import argparse
import os
import sys

import httpx
from sqlalchemy import select

from app.config import BACKEND_DIR, PRODUCT_NAME, get_settings
from app.db import SessionLocal
from app.models import Responder
from app.services.calls import TWILIO_API

OK, BAD, WARN = "OK  ", "FAIL", "WARN"


def tail(phone: str) -> str:
    return f"...{phone[-4:]}" if phone else "(none)"


def call_list() -> list[Responder]:
    with SessionLocal() as db:
        return list(db.scalars(select(Responder).where(Responder.active.is_(True)).order_by(Responder.name)))


def main() -> int:
    parser = argparse.ArgumentParser(description="Check the Twilio setup for alert calls.")
    parser.add_argument("--call", metavar="NAME", help="ring this person on the call list (name, any case)")
    args = parser.parse_args()
    s = get_settings()
    problems = 0

    def line(status: str, text: str) -> None:
        nonlocal problems
        problems += status == BAD
        print(f"[{status}] {text}")

    print(f"CALLS_MODE={s.calls_mode}  (live = real calls; simulate = pretend; auto = live when the keys are set)")
    print(f"settings read from {BACKEND_DIR / '.env'}")
    for key in ("CALLS_MODE", "GROQ_MODEL"):
        if key in os.environ:  # a Windows/PowerShell variable wins over .env
            line(WARN, f"{key} is also set as a Windows environment variable ({os.environ[key]!r}); it wins over .env. "
                       f"Remove it: PowerShell  Remove-Item Env:{key}  (and in System > Environment Variables)")
    env_file = BACKEND_DIR / ".env"
    if env_file.exists():
        seen = [ln.split("=", 1)[1].strip() for ln in env_file.read_text(encoding="utf-8", errors="replace").splitlines()
                if ln.strip().upper().startswith("CALLS_MODE=")]
        if len(seen) > 1:
            line(WARN, f".env has {len(seen)} CALLS_MODE lines ({', '.join(seen)}); the LAST one wins. Keep one.")
    if s.calls_mode != "live":
        line(BAD, f"calls are not real: CALLS_MODE={s.calls_mode}. Put CALLS_MODE=live in the .env above, save "
                  "(Ctrl+S), restart the API. Alerts will not ring anyone until then.")
    for key in ("twilio_account_sid", "twilio_auth_token", "twilio_from_number", "public_base_url"):
        value = getattr(s, key)
        line(OK if value else BAD, f"{key.upper()}: {'set' if value else 'MISSING'}")
    if not (s.twilio_account_sid and s.twilio_auth_token):
        print("Fill the Twilio keys in backend/.env (console.twilio.com, Account info), then run this again.")
        return 1

    auth = (s.twilio_account_sid, s.twilio_auth_token)
    base = f"{TWILIO_API}/Accounts/{s.twilio_account_sid}"
    try:
        account = httpx.get(f"{base}.json", auth=auth, timeout=10)
    except httpx.HTTPError as exc:
        line(BAD, f"cannot reach Twilio: {exc}")
        return 1
    if account.status_code == 401:
        line(BAD, "Twilio refused the SID / auth token (401). Copy both again from console.twilio.com.")
        return 1
    info = account.json()
    trial = info.get("type") == "Trial"
    line(OK if info.get("status") == "active" else BAD,
         f"Twilio account {info.get('status')}, {'TRIAL (verified numbers only)' if trial else 'full'}")

    owned = httpx.get(f"{base}/IncomingPhoneNumbers.json", params={"PhoneNumber": s.twilio_from_number},
                      auth=auth, timeout=10).json().get("incoming_phone_numbers", [])
    line(OK if owned else BAD, f"TWILIO_FROM_NUMBER {tail(s.twilio_from_number)} "
         f"{'belongs to this account' if owned else 'is NOT a number of this account (buy or copy it in the console)'}")

    people = call_list()
    verified = {c["phone_number"] for c in httpx.get(f"{base}/OutgoingCallerIds.json", params={"PageSize": 200},
                                                     auth=auth, timeout=10).json().get("outgoing_caller_ids", [])}
    if not people:
        line(BAD, "the call list is empty: run python -m app.seed (phones from DEMO_RESPONDER_PHONES)")
    for person in people:
        if not trial:
            line(OK, f"{person.name} {tail(person.phone)} ({person.zone})")
        elif person.phone in verified:
            line(OK, f"{person.name} {tail(person.phone)} is verified")
        else:
            line(WARN, f"{person.name} {tail(person.phone)} is NOT verified: Twilio will refuse to call them. "
                       "Console > Phone Numbers > Verified Caller IDs > Add")

    if s.public_base_url and not s.public_base_url.startswith(("https://", "http://")):
        line(BAD, "PUBLIC_BASE_URL must be the full https address ngrok shows, e.g. https://ab12-34.ngrok-free.app "
                  "(calls only speak the problem until this is fixed: no keypresses or speech come back)")
    elif s.public_base_url:
        url = s.public_base_url.rstrip("/") + "/twilio/status?call_id=0"
        try:
            reply = httpx.post(url, data={}, timeout=10)
            if reply.status_code == 403 and "signature" in reply.text:
                line(OK, "PUBLIC_BASE_URL reaches this API (it refused an unsigned request, as it should)")
            else:
                line(BAD, f"PUBLIC_BASE_URL answered {reply.status_code}: is ngrok running (ngrok http 8000) and "
                          "is the URL in .env the one ngrok shows now? It changes every time ngrok restarts.")
        except httpx.HTTPError as exc:
            line(BAD, f"PUBLIC_BASE_URL not reachable ({exc.__class__.__name__}): start ngrok http 8000")

    print(f"CALL_STYLE={s.call_style}  CALL_VOICE={s.call_voice}")
    if s.call_style == "conversation":
        if not (s.groq_api_key and s.groq_model):
            line(WARN, "GROQ_API_KEY or GROQ_MODEL missing: Friday can only use fixed sentences on calls")
        else:
            try:
                from app.services.llm import LlmUnavailable, chat_json

                value, source = chat_json([{"role": "user", "content": 'Reply with JSON {"ok": true}'}])
                if source == "groq":
                    line(OK, f"Groq answers ({s.groq_model}): Friday can talk freely on calls")
                else:
                    line(BAD, f"Groq failed with GROQ_MODEL={s.groq_model} (see the message above); only the "
                              "offline model answered, which is slow on calls. Pick a model from "
                              "console.groq.com/docs/models and put it in GROQ_MODEL.")
            except LlmUnavailable as exc:
                line(BAD, f"Groq did not answer ({exc}): check GROQ_API_KEY and GROQ_MODEL "
                          "(e.g. llama-3.3-70b-versatile). Until then calls use fixed sentences.")

    if args.call:
        person = next((p for p in people if p.name.lower() == args.call.lower()), None)
        if person is None:
            line(BAD, f"{args.call!r} is not on the call list ({', '.join(p.name for p in people)})")
        else:
            twiml = (f'<Response><Say language="{s.call_language}">This is a test call from {PRODUCT_NAME}. '
                     "Alert calls will sound like this. Goodbye.</Say></Response>")
            placed = httpx.post(f"{base}/Calls.json", auth=auth, timeout=10,
                                data={"To": person.phone, "From": s.twilio_from_number, "Twiml": twiml})
            if placed.status_code < 300:
                line(OK, f"test call placed to {person.name} {tail(person.phone)}: their phone should ring now "
                         "(this test dials directly, even when CALLS_MODE is not live; if it does not ring, look "
                         "at console.twilio.com > Monitor > Calls for the reason)")
            else:
                line(BAD, f"Twilio refused the test call ({placed.status_code}): "
                          f"{placed.json().get('message', placed.text[:200])}")

    print("\nAll good: press Demo alert and the phones ring." if problems == 0
          else f"\n{problems} problem(s) above. Fix them and run this again.")
    return 0 if problems == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
