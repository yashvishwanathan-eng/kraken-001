"""Check the Rumik key in .env without showing it. Run on the laptop, from backend/:

    python -m tools.check_rumik
    python -m tools.check_rumik --save      also writes media/ack/yes_boss_rumik.wav ("Yes, boss.")
    python -m tools.check_rumik --text "Hey Friday" --out media/debug/hey_friday.wav   any sentence to a file
    python -m tools.check_rumik --voices    one sample per speaker (speaker_0..speaker_9) in media/debug/voices/:
                                            listen, pick the woman's voice you like, set RUMIK_SPEAKER=speaker_N

Prints OK with the time it took, or the reason it failed. The key itself is never printed.
"""

import argparse
import sys

from app.config import BACKEND_DIR, get_settings
from app.services import tts


def voices(s) -> int:
    """Without a fixed speaker Rumik may pick a different voice per sentence (sometimes a man's). Save one sample
    per speaker so the right one can be chosen once and kept."""
    folder = BACKEND_DIR / "media" / "debug" / "voices"
    folder.mkdir(parents=True, exist_ok=True)
    saved = 0
    for n in range(10):
        s.rumik_speaker = f"speaker_{n}"
        try:
            audio, ms = tts.speak("Yes, boss. Fire detected on camera two. I am calling the head guard.")
        except tts.TtsUnavailable as exc:
            print(f"speaker_{n}: not available ({exc})")
            continue
        (folder / f"speaker_{n}.wav").write_bytes(audio)
        saved += 1
        print(f"speaker_{n}: saved ({ms} ms)")
    print(f"\n{saved} samples in {folder}. Play them, then in .env set RUMIK_SPEAKER=speaker_N (the woman's voice),"
          " delete media/ack/yes_boss_rumik.wav so 'Yes, boss' is made again, and restart the API.")
    return 0 if saved else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--save", action="store_true", help="save the test sentence as the wake-word reply")
    parser.add_argument("--text", default="Yes, boss.")
    parser.add_argument("--out", help="save the audio to this WAV file (relative to backend/)")
    parser.add_argument("--voices", action="store_true", help="save a sample of each speaker to pick one")
    args = parser.parse_args()
    s = get_settings()
    if args.voices:
        return voices(s)
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
