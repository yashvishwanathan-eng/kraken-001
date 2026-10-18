"""One worker process per camera.

    python -m worker.run_worker --camera cam-webcam

Day 1: read the camera, run person detection + tracking on every Nth frame,
print analysed FPS. Rules, clips, heartbeats and the MJPEG server come later.
"""

import argparse
import logging
import sys
import time

import cv2

from app.config import get_settings, load_cameras
from worker.detectors import PersonTracker

RECONNECT_DELAY_S = 5
FPS_REPORT_EVERY_S = 5.0

log = logging.getLogger("safetyai.worker")


def parse_source(source: str) -> int | str:
    """'0' means the laptop webcam index; anything else (URL or file path) is passed through."""
    return int(source) if source.isdigit() else source


def open_capture(source: str) -> cv2.VideoCapture | None:
    cap = cv2.VideoCapture(parse_source(source))
    return cap if cap.isOpened() else None


def run(camera_id: str, max_seconds: float, source_override: str | None) -> int:
    settings = get_settings()
    cameras = {cam.id: cam for cam in load_cameras()}
    if camera_id not in cameras:
        log.error("unknown camera %s; known: %s", camera_id, ", ".join(cameras))
        return 2
    camera = cameras[camera_id]
    if source_override is not None:
        camera = camera.model_copy(update={"source": source_override})

    tracker = PersonTracker(settings.yolo_model_path, settings.detect_imgsz, settings.detect_conf)
    tracker.warm_up()

    started = time.monotonic()
    window_start = started
    analysed_in_window = 0
    frame_index = 0
    cap = None

    try:
        while max_seconds <= 0 or time.monotonic() - started < max_seconds:
            if cap is None:
                cap = open_capture(camera.source)
                if cap is None:
                    log.warning("camera %s offline, retrying in %ss", camera.id, RECONNECT_DELAY_S)
                    time.sleep(RECONNECT_DELAY_S)
                    continue
                log.info("camera %s opened (source=%s)", camera.id, camera.source)

            ok, frame = cap.read()
            if not ok:
                log.warning("camera %s lost, reconnecting in %ss", camera.id, RECONNECT_DELAY_S)
                cap.release()
                cap = None
                time.sleep(RECONNECT_DELAY_S)
                continue

            frame_index += 1
            if frame_index % settings.detect_every_n_frames != 0:
                continue

            t0 = time.perf_counter()
            tracks = tracker.track(frame)
            detect_ms = (time.perf_counter() - t0) * 1000
            analysed_in_window += 1
            log.debug("camera=%s frame=%d persons=%d detect_ms=%.1f", camera.id, frame_index, len(tracks), detect_ms)

            now = time.monotonic()
            if now - window_start >= FPS_REPORT_EVERY_S:
                fps = analysed_in_window / (now - window_start)
                print(f"[{camera.id}] analysed FPS={fps:.1f} persons_now={len(tracks)}", flush=True)
                window_start = now
                analysed_in_window = 0
    except KeyboardInterrupt:
        pass
    finally:
        if cap is not None:
            cap.release()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="SafetyAI camera worker")
    parser.add_argument("--camera", required=True, help="camera id from config/cameras.yaml")
    parser.add_argument("--seconds", type=float, default=0, help="stop after N seconds (0 = run forever)")
    parser.add_argument("--source", help="testing only: use this video file or URL instead of the config source")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    return run(args.camera, args.seconds, args.source)


if __name__ == "__main__":
    sys.exit(main())
