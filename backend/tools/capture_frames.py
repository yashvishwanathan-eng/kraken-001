"""Take training photos from a camera: one photo every --every seconds for --seconds, into a folder.

    python -m tools.capture_frames --source 0 --name lighter --seconds 90
    python -m tools.capture_frames --source http://192.168.137.74:8080/video --name not_fire --seconds 30

A window shows the camera; press Q to stop early. Photos go to media/training/<name>/<name>_0001.jpg. Upload
the folder to the Roboflow project, label the flames, and train (backend/tools/vision_training/README.md, Part 3b).
"""

import argparse
import time

import cv2

from app.config import media_root
from worker.capture import parse_source


def main() -> int:
    parser = argparse.ArgumentParser(description="take training photos from a camera")
    parser.add_argument("--source", default="0", help="0 = laptop webcam, or a phone URL like http://IP:8080/video")
    parser.add_argument("--name", required=True, help="folder and file name, e.g. lighter or not_fire")
    parser.add_argument("--seconds", type=float, default=90, help="how long to record")
    parser.add_argument("--every", type=float, default=0.5, help="seconds between photos")
    args = parser.parse_args()

    folder = media_root() / "training" / args.name
    folder.mkdir(parents=True, exist_ok=True)
    cap = cv2.VideoCapture(parse_source(args.source))
    if not cap.isOpened():
        print(f"cannot open camera {args.source!r}: close other programs using it (the app's camera worker too)")
        return 1
    start = last = time.monotonic()
    count = len(list(folder.glob("*.jpg")))
    print(f"recording {args.seconds:.0f} s into {folder} (Q to stop)")
    while time.monotonic() - start < args.seconds:
        ok, frame = cap.read()
        if not ok:
            continue
        now = time.monotonic()
        if now - last >= args.every:
            count += 1
            cv2.imwrite(str(folder / f"{args.name}_{count:04d}.jpg"), frame)
            last = now
        shown = frame.copy()
        cv2.putText(shown, f"{args.name}: {count} photos, {args.seconds - (now - start):.0f} s left", (10, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2)
        cv2.imshow("capture (Q to stop)", shown)
        if cv2.waitKey(1) & 0xFF == ord("q"):
            break
    cap.release()
    cv2.destroyAllWindows()
    print(f"{count} photos in {folder}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
