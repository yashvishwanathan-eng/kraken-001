"""Run the wake-word listener on this laptop.

  python -m voice.run_listener --list-devices     show the microphones (pick the built-in one)
  python -m voice.run_listener                    listen with the settings in .env
  python -m voice.run_listener --device 2 --threshold 0.6
"""

import argparse
import logging

from voice.wake import WakeListener, list_devices


def main() -> None:
    parser = argparse.ArgumentParser(description="Listen for the wake word and play the acknowledgement.")
    parser.add_argument("--list-devices", action="store_true", help="list microphones and exit")
    parser.add_argument("--device", help="microphone number or name (see --list-devices)")
    parser.add_argument("--threshold", type=float, help="0 to 1; higher is stricter (default from .env)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    if args.list_devices:
        print(list_devices())
        return
    WakeListener(threshold=args.threshold, device=args.device).run()


if __name__ == "__main__":
    main()
