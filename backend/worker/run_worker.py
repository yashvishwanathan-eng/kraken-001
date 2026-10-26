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

from app.config import get_settings, media_root
from app.db import SessionLocal
from app.models import FaceSample, User, Zone
from app.services.camera_registry import all_cameras
from app.services.faces import FaceBank, from_bytes
from app.services.faces import engine as face_engine
from worker.buffer import RingBuffer
from worker.capture import LatestFrameCapture
from worker.detectors import BagDetector, Person, PersonPoseTracker, hip_y, optional_detector, torso_angle_deg
from worker.heartbeat import HeartbeatSender
from worker.incidents import IncidentReporter
from worker.mjpeg import MjpegServer
from worker.recorder import ContinuousRecorder
from worker.rules import BagObs, DetectionObs, PersonObs, RuleConfig, RulesEngine, ZoneDef, point_in_polygon

REPORT_EVERY_S = 5.0
ZONE_REFRESH_S = 30.0
IDLE_SLEEP_S = 0.005
JPEG_QUALITY = 80
ZONE_COLOURS = {"restricted": (0, 0, 255), "ppe": (0, 165, 255), "work": (0, 255, 255)}  # BGR

log = logging.getLogger("safetyai.worker")


def load_zones(camera_id: str) -> list[ZoneDef] | None:
    """Zones for this camera from the database (with the names allowed into restricted ones). None if the database
    is unreachable."""
    try:
        with SessionLocal() as db:
            rows = db.scalars(select(Zone).where(Zone.camera_id == camera_id)).all()
            names = dict(db.execute(select(User.id, User.name)).all())
            return [ZoneDef(z.name, z.kind, [tuple(p) for p in z.polygon], z.max_people,
                            frozenset(names[u] for u in (z.allowed_user_ids or []) if u in names)) for z in rows]
    except Exception:
        log.warning("camera=%s could not load zones from the database", camera_id, exc_info=True)
        return None


def load_face_bank() -> FaceBank | None:
    """Every enrolled face fingerprint of an active person. None if the database is unreachable."""
    try:
        with SessionLocal() as db:
            rows = db.execute(select(User.name, FaceSample.embedding).join(User, User.id == FaceSample.user_id)
                              .where(User.active.is_(True))).all()
        return FaceBank([(name, from_bytes(blob)) for name, blob in rows])
    except Exception:
        log.warning("could not load face fingerprints from the database", exc_info=True)
        return None


class FaceWatcher:
    """Who is each person standing in a restricted zone? Looks for a face in the top of their box, compares it
    with the enrolled people, and remembers a name once recognised (a track keeps its name while it lasts)."""

    def __init__(self, max_per_frame: int = 3) -> None:
        self.engine = face_engine()
        self.bank = FaceBank([])
        self.max_per_frame = max_per_frame
        self.identities: dict[int, str] = {}

    def update(self, image: np.ndarray, people: list[Person], zones: list[ZoneDef]) -> dict[int, str]:
        h, w = image.shape[:2]
        restricted = [z for z in zones if z.kind == "restricted"]
        alive = {p.track_id for p in people}
        self.identities = {t: n for t, n in self.identities.items() if t in alive}
        checked = 0
        for p in people:
            if checked >= self.max_per_frame or self.identities.get(p.track_id):
                continue  # already recognised: no need to look again
            x1, y1, x2, y2 = p.xyxy
            feet = ((x1 + x2) / 2 / w, y2 / h)
            if not any(point_in_polygon(feet, z.polygon) for z in restricted):
                continue
            pad = (x2 - x1) * 0.15
            top = image[max(0, int(y1 - pad)):int(y1 + (y2 - y1) * 0.45), max(0, int(x1 - pad)):int(x2 + pad)]
            checked += 1
            if top.size == 0:
                continue
            found = self.engine.faces(top)
            if not found:
                continue
            name, _score = self.bank.match(found[0].embedding)
            self.identities[p.track_id] = name or ""
        return self.identities


def to_obs(person: Person) -> PersonObs:
    return PersonObs(person.track_id, person.xyxy, person.confidence,
                     torso_angle_deg(person.keypoints), hip_y(person.keypoints))


def annotate(image: np.ndarray, zones: list[ZoneDef], people: list[Person], fps_text: str,
             names: dict[int, str] | None = None) -> np.ndarray:
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
        name = (names or {}).get(person.track_id)
        label = f"{name}" if name else f"ID {person.track_id}{angle_text}"  # a recognised face shows its name
        cv2.putText(out, label, (x1, max(y1 - 6, 12)),
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
    # flames are kept from FIRE_CONF up (a lighter's flame scores lower); the rule still needs FIRE_FRAMES in a row
    fire_detector = optional_detector(settings.fire_model_path, settings.detect_imgsz,
                                      min(settings.detect_conf, settings.fire_conf), "fire")
    helmet_detector = optional_detector(settings.helmet_model_path, settings.detect_imgsz, settings.detect_conf,
                                        "rider helmet")
    for extra in (ppe_detector, fire_detector, helmet_detector):
        if extra is not None:
            extra.warm_up()

    zones = load_zones(camera.id) or []
    faces_on = None
    if settings.face_enabled and any(z.kind == "restricted" for z in zones):
        try:
            faces_on = FaceWatcher()
            faces_on.bank = load_face_bank() or faces_on.bank
            log.info("camera %s: face recognition on, %d fingerprints enrolled", camera.id, len(faces_on.bank))
        except Exception:  # model download failed (offline first run): restricted zones still alert as before
            log.warning("camera %s: face recognition off (models not available)", camera.id, exc_info=True)
    engine = RulesEngine(zones, RuleConfig.from_settings(settings))
    ring = RingBuffer(settings.ring_buffer_s, settings.ring_max_width)
    reporter = IncidentReporter(camera.id, settings, ring)
    mjpeg = MjpegServer(camera.port).start()
    recorder = None
    if settings.record_enabled:
        recorder = ContinuousRecorder(camera.id, media_root() / "recordings", settings.record_fps,
                                      settings.record_segment_s, settings.record_max_width, settings.record_crf,
                                      settings.record_days, settings.record_min_free_gb).start()

    def on_frame(image) -> None:  # every camera frame: the incident clip buffer and the all-day recording
        ring.add(image)
        if recorder is not None:
            recorder.push(image)

    capture = LatestFrameCapture(source, settings.detect_every_n_frames, on_frame=on_frame).start()
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
                if faces_on is not None:  # newly enrolled faces count within ZONE_REFRESH_S
                    faces_on.bank = load_face_bank() or faces_on.bank
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
            riders = None
            if helmet_detector is not None:
                riders = [DetectionObs(d.xyxy, d.label, d.confidence) for d in helmet_detector.detect(keyframe.image)]
            names = faces_on.update(keyframe.image, people, zones) if faces_on is not None else None
            candidates = engine.update(now, datetime.now(timezone.utc), w, h, [to_obs(p) for p in people], bags,
                                       ppe=ppe, fire=fire, riders=riders, identities=names)

            analysed_in_window += 1
            if now - window_start >= REPORT_EVERY_S:
                analysed_fps = analysed_in_window / (now - window_start)
                live["fps"] = analysed_fps
                fps_text = f"analysed {analysed_fps:.1f} fps | camera {capture.camera_fps:.1f} fps"
                print(f"[{camera.id}] {fps_text} | persons={len(people)} | detect {detect_ms:.0f} ms", flush=True)
                window_start, analysed_in_window = now, 0

            ok, jpeg = cv2.imencode(".jpg", annotate(keyframe.image, zones, people, fps_text, names),
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
        if recorder is not None:
            recorder.stop()
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
