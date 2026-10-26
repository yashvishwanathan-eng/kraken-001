"""Check the Rumik key in .env without showing it. Run on the laptop, from backend/:

    python -m tools.check_rumik
    python -m tools.check_rumik --save      also writes media/ack/yes_boss_rumik.wav ("Yes, boss.")
    python -m tools.check_rumik --text "Hey Jarvis" --out media/debug/hey_jarvis.wav   any sentence to a file

Prints OK with the time it took, or the reason it failed. The key itself is never printed.
"""

import argparse
import sys

from app.config import BACKEND_DIR, get_settings
from app.services import tts


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--save", action="store_true", help="save the test sentence as the wake-word reply")
    parser.add_argument("--text", default="Yes, boss.")
    parser.add_argument("--out", help="save the audio to this WAV file (relative to backend/)")
    args = parser.parse_args()
    s = get_settings()
    key = f"set ({len(s.rumik_api_key)} characters)" if s.rumik_api_key else "MISSING"
    print(f"RUMIK_API_KEY:     {key}")
    print(f"RUMIK_GATEWAY_URL: {s.rumik_gateway_url or 'MISSING (copy it from the Rumik dashboard)'}")
    print(f"RUMIK_MODEL:       {s.rumik_model}")
    print(f"RUMIK_SPEAKER:     {s.rumik_speaker or '(Rumik default)'}")
    print(f"RUMIK_DESCRIPTION: {s.rumik_description or '(none)'}")
    try:
        audio, ms = tts.speak(args.text)
    except tts.TtsUnavailable as exc:
        print(f"FAILED: {exc}")
        return 1
    print(f"OK: Rumik answered with {len(audio) // 1024} KB of audio in {ms} ms")
    if args.out:
        out = BACKEND_DIR / args.out
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(audio)
        print(f"saved {out}")
    if args.save:
        out = BACKEND_DIR / "media" / "ack" / "yes_boss_rumik.wav"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(audio)
        print(f"saved {out}. To use it as the wake reply, set WAKE_ACK_SOUND=media/ack/yes_boss_rumik.wav")
    return 0


if __name__ == "__main__":
    sys.exit(main())
