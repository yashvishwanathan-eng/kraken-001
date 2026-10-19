"""One worker process per camera.

    python -m worker.run_worker --camera cam-webcam

A capture thread reads the camera nonstop (see worker/capture.py). This loop analyses the
newest keyframe, draws boxes, track IDs and FPS, publishes the annotated JPEG on the local
MJPEG port, and prints torso angles. Rules, clips and heartbeats come later.
"""

import argparse
import logging
import sys
import time

import cv2

from app.config import get_settings, load_cameras
from worker.capture import LatestFrameCapture
from worker.detectors import Person, PersonPoseTracker, torso_angle_deg
from worker.mjpeg import MjpegServer

REPORT_EVERY_S = 5.0
IDLE_SLEEP_S = 0.005
JPEG_QUALITY = 80

log = logging.getLogger("safetyai.worker")


def annotate(image, people: list[Person], fps_text: str):
    out = image.copy()
    for person in people:
        x1, y1, x2, y2 = (int(v) for v in person.xyxy)
        cv2.rectangle(out, (x1, y1), (x2, y2), (0, 200, 0), 2)
        angle = torso_angle_deg(person.keypoints)
        angle_text = "" if angle is None else f" {angle:.0f}deg"
        cv2.putText(out, f"ID {person.track_id}{angle_text}", (x1, max(y1 - 6, 12)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 200, 0), 1)
    cv2.putText(out, fps_text, (8, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)
    return out


def run(camera_id: str, max_seconds: float, source_override: str | None) -> int:
    settings = get_settings()
    cameras = {cam.id: cam for cam in load_cameras()}
    if camera_id not in cameras:
        log.error("unknown camera %s; known: %s", camera_id, ", ".join(cameras))
        return 2
    camera = cameras[camera_id]
    source = source_override if source_override is not None else camera.source

    tracker = PersonPoseTracker(settings.yolo_model_path, settings.detect_imgsz, settings.detect_conf)
    tracker.warm_up()
    mjpeg = MjpegServer(camera.port).start()
    capture = LatestFrameCapture(source, settings.detect_every_n_frames).start()
    log.info("camera %s: stream on http://127.0.0.1:%d/stream", camera.id, camera.port)

    started = time.monotonic()
    window_start = started
    analysed_in_window = 0
    last_analysed_id = -1
    fps_text = "starting..."

    try:
        while max_seconds <= 0 or time.monotonic() - started < max_seconds:
            keyframe = capture.latest_keyframe()
            if keyframe is None or keyframe.frame_id == last_analysed_id:
                time.sleep(IDLE_SLEEP_S)  # nothing new yet
                continue
            last_analysed_id = keyframe.frame_id

            t0 = time.perf_counter()
            people = tracker.track(keyframe.image)
            detect_ms = (time.perf_counter() - t0) * 1000
            analysed_in_window += 1

            now = time.monotonic()
            if now - window_start >= REPORT_EVERY_S:
                analysed_fps = analysed_in_window / (now - window_start)
                fps_text = f"analysed {analysed_fps:.1f} fps | camera {capture.camera_fps:.1f} fps"
                angles = ", ".join(
                    f"ID {p.track_id}: {torso_angle_deg(p.keypoints):.0f}deg"
                    for p in people if torso_angle_deg(p.keypoints) is not None
                )
                print(f"[{camera.id}] {fps_text} | persons={len(people)} | detect {detect_ms:.0f} ms"
                      + (f" | torso {angles}" if angles else ""), flush=True)
                window_start, analysed_in_window = now, 0

            ok, jpeg = cv2.imencode(".jpg", annotate(keyframe.image, people, fps_text),
                                    [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
            if ok:
                mjpeg.publish(jpeg.tobytes())
    except KeyboardInterrupt:
        pass
    finally:
        capture.stop()
        mjpeg.stop()
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
