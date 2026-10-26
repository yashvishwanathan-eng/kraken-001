"""Run Friday's wake-word listener on this laptop (start the API first).

  python -m voice.run_listener --list-devices     show the microphones (pick the built-in one)
  python -m voice.run_listener                    listen with the settings in .env
  python -m voice.run_listener --device 2 --threshold 0.6
  python -m voice.run_listener --ack-only         only play "Yes, boss" (the old Step 1 test)

On "Hey Friday" it answers "Yes, boss", opens Friday's page in the browser when the software is not open,
records your order until you stop talking, and Friday answers (on the open dashboard, or on this speaker).
Needs INTERNAL_KEY and FRIDAY_USER in .env.
"""

import argparse
import io
import logging
import wave
import webbrowser

from app.config import get_settings
from voice.api_client import ApiClient
from voice.session import Session, record_until_silence
from voice.wake import SAMPLE_RATE, WakeListener, list_devices

log = logging.getLogger("safetyai.voice")


def play_wav(data: bytes) -> None:
    """Play a WAV on the laptop speaker and wait until it ends (so the mic does not hear Friday)."""
    import numpy as np
    import sounddevice as sd

    with wave.open(io.BytesIO(data)) as w:
        frames = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
        if w.getnchannels() > 1:
            frames = frames.reshape(-1, w.getnchannels())
        sd.play(frames, w.getframerate(), blocking=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Friday's wake-word listener.")
    parser.add_argument("--list-devices", action="store_true", help="list microphones and exit")
    parser.add_argument("--device", help="microphone number or name (see --list-devices)")
    parser.add_argument("--threshold", type=float, help="0 to 1; higher is stricter (default from .env)")
    parser.add_argument("--ack-only", action="store_true", help='only answer "Yes, boss" (no recording)')
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    if args.list_devices:
        print(list_devices())
        return
    listener = WakeListener(threshold=args.threshold, device=args.device)
    s = get_settings()
    if args.ack_only:
        listener.run()
        return
    if not s.internal_key or not s.friday_user:
        log.error("set INTERNAL_KEY and FRIDAY_USER in .env (or run with --ack-only)")
        return

    session = Session(ApiClient(s.api_base_url, s.internal_key), play=play_wav, open_browser=webbrowser.open,
                      dashboard_url=s.dashboard_url)

    def on_wake(next_chunks) -> None:
        def record() -> bytes:
            return record_until_silence(next_chunks(), sample_rate=SAMPLE_RATE, silence_s=s.voice_silence_s,
                                        max_s=s.voice_max_s, start_timeout_s=s.voice_start_timeout_s,
                                        rms_threshold=s.voice_silence_rms)
        try:
            session.turn(record)
        except Exception:  # the API is down or slow: say so in the log and keep listening
            log.exception("Friday could not finish this turn; listening again")

    listener.run(on_trigger=on_wake)


if __name__ == "__main__":
    main()
