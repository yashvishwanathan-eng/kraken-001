"""One worker process per camera.

    python -m worker.run_worker --camera cam-webcam

Pipeline for every analysed keyframe:
  capture thread -> pose tracker (+ bag detector about once a second) -> rules engine
  -> incidents stored (snapshot now, 10 s clip a few seconds later) -> API told
The annotated frame (zones, boxes, IDs, angles, FPS) is published on the local MJPEG port.
"""

import argparse
import logging
import sys
import time
from datetime import datetime, timezone

import cv2
import numpy as np
from sqlalchemy import select

from app.config import get_settings
from app.db import SessionLocal
from app.models import Zone
from app.services.camera_registry import all_cameras
from worker.buffer import RingBuffer
from worker.capture import LatestFrameCapture
from worker.detectors import BagDetector, Person, PersonPoseTracker, hip_y, optional_detector, torso_angle_deg
from worker.heartbeat import HeartbeatSender
from worker.incidents import IncidentReporter
from worker.mjpeg import MjpegServer
from worker.rules import BagObs, DetectionObs, PersonObs, RuleConfig, RulesEngine, ZoneDef

REPORT_EVERY_S = 5.0
ZONE_REFRESH_S = 30.0
IDLE_SLEEP_S = 0.005
JPEG_QUALITY = 80
ZONE_COLOURS = {"restricted": (0, 0, 255), "ppe": (0, 165, 255), "work": (0, 255, 255)}  # BGR

log = logging.getLogger("safetyai.worker")


def load_zones(camera_id: str) -> list[ZoneDef] | None:
    """Zones for this camera from the database. None if the database is unreachable."""
    try:
        with SessionLocal() as db:
            rows = db.scalars(select(Zone).where(Zone.camera_id == camera_id)).all()
            return [ZoneDef(z.name, z.kind, [tuple(p) for p in z.polygon], z.max_people) for z in rows]
    except Exception:
        log.warning("camera=%s could not load zones from the database", camera_id, exc_info=True)
        return None


def to_obs(person: Person) -> PersonObs:
    return PersonObs(person.track_id, person.xyxy, person.confidence,
                     torso_angle_deg(person.keypoints), hip_y(person.keypoints))


def annotate(image: np.ndarray, zones: list[ZoneDef], people: list[Person], fps_text: str) -> np.ndarray:
    out = image.copy()
    h, w = out.shape[:2]
    for zone in zones:
        points = np.array([[int(x * w), int(y * h)] for x, y in zone.polygon], dtype=np.int32)
        colour = ZONE_COLOURS.get(zone.kind, (200, 200, 200))
        cv2.polylines(out, [points], isClosed=True, color=colour, thickness=2)
        cv2.putText(out, zone.name, tuple(points[0]), cv2.FONT_HERSHEY_SIMPLEX, 0.5, colour, 1)
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
    cameras = {cam.id: cam for cam in all_cameras()}  # database first (dashboard-added cameras), then yaml
    if camera_id not in cameras:
        log.error("unknown camera %s; known: %s", camera_id, ", ".join(cameras))
        return 2
    camera = cameras[camera_id]
    source = source_override if source_override is not None else camera.source

    tracker = PersonPoseTracker(settings.yolo_model_path, settings.detect_imgsz, settings.detect_conf)
    tracker.warm_up()
    bag_detector = BagDetector(settings.bag_model_path, settings.detect_imgsz, settings.detect_conf)
    bag_detector.warm_up()
    # PPE and fire/smoke need custom models; each rule stays off until its model path is set in .env
    ppe_detector = optional_detector(settings.ppe_model_path, settings.detect_imgsz, settings.detect_conf, "PPE")
    fire_detector = optional_detector(settings.fire_model_path, settings.detect_imgsz, settings.detect_conf, "fire")
    for extra in (ppe_detector, fire_detector):
        if extra is not None:
            extra.warm_up()

    zones = load_zones(camera.id) or []
    engine = RulesEngine(zones, RuleConfig.from_settings(settings))
    ring = RingBuffer(settings.ring_buffer_s, settings.ring_max_width)
    reporter = IncidentReporter(camera.id, settings, ring)
    mjpeg = MjpegServer(camera.port).start()
    capture = LatestFrameCapture(source, settings.detect_every_n_frames, on_frame=ring.add).start()
    live = {"fps": 0.0}  # analysed fps, read by the heartbeat thread
    heartbeat = HeartbeatSender(camera.id, settings, lambda: (capture.online, live["fps"])).start()
    log.info("camera %s: %d zones, stream on http://127.0.0.1:%d/stream", camera.id, len(zones), camera.port)

    started = time.monotonic()
    window_start, analysed_in_window = started, 0
    last_analysed_id = -1
    last_bag_check = last_zone_refresh = started
    fps_text = "starting..."

    try:
        while max_seconds <= 0 or time.monotonic() - started < max_seconds:
            keyframe = capture.latest_keyframe()
            if keyframe is None or keyframe.frame_id == last_analysed_id:
                time.sleep(IDLE_SLEEP_S)  # nothing new yet
                continue
            last_analysed_id = keyframe.frame_id
            now = time.monotonic()

            if now - last_zone_refresh >= ZONE_REFRESH_S:
                refreshed = load_zones(camera.id)
                if refreshed is not None:  # keep the old zones if the database is briefly down
                    zones = refreshed
                    engine.set_zones(zones)
                last_zone_refresh = now

            t0 = time.perf_counter()
            people = tracker.track(keyframe.image)
            bags = None
            if now - last_bag_check >= settings.bag_detect_interval_s:
                bags = [BagObs(b.xyxy, b.label, b.confidence) for b in bag_detector.detect(keyframe.image)]
                last_bag_check = now
            detect_ms = (time.perf_counter() - t0) * 1000

            h, w = keyframe.image.shape[:2]
            ppe = fire = None
            if ppe_detector is not None and any(z.kind == "ppe" for z in zones):
                ppe = [DetectionObs(d.xyxy, d.label, d.confidence) for d in ppe_detector.detect(keyframe.image)]
            if fire_detector is not None:
                fire = [DetectionObs(d.xyxy, d.label, d.confidence) for d in fire_detector.detect(keyframe.image)]
            candidates = engine.update(now, datetime.now(timezone.utc), w, h, [to_obs(p) for p in people], bags,
                                       ppe=ppe, fire=fire)

            analysed_in_window += 1
            if now - window_start >= REPORT_EVERY_S:
                analysed_fps = analysed_in_window / (now - window_start)
                live["fps"] = analysed_fps
                fps_text = f"analysed {analysed_fps:.1f} fps | camera {capture.camera_fps:.1f} fps"
                print(f"[{camera.id}] {fps_text} | persons={len(people)} | detect {detect_ms:.0f} ms", flush=True)
                window_start, analysed_in_window = now, 0

            ok, jpeg = cv2.imencode(".jpg", annotate(keyframe.image, zones, people, fps_text),
                                    [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
            if not ok:
                continue
            jpeg_bytes = jpeg.tobytes()
            mjpeg.publish(jpeg_bytes)
            for candidate in candidates:
                incident_id = reporter.report(candidate, jpeg_bytes, now)
                print(f"[{camera.id}] INCIDENT {incident_id} {candidate.type} ({candidate.severity}) "
                      f"zone={candidate.zone} {candidate.reason}", flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        heartbeat.stop()
        capture.stop()
        mjpeg.stop()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="RingGuard camera worker")
    parser.add_argument("--camera", required=True, help="camera id from config/cameras.yaml")
    parser.add_argument("--seconds", type=float, default=0, help="stop after N seconds (0 = run forever)")
    parser.add_argument("--source", help="testing only: use this video file or URL instead of the config source")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    return run(args.camera, args.seconds, args.source)


if __name__ == "__main__":
    sys.exit(main())
