"""One worker process per camera.

    python -m worker.run_worker --camera cam-webcam

Pipeline for every analysed keyframe:
  capture thread -> pose tracker (+ bag detector about once a second) -> rules engine
  -> incidents stored (snapshot now, 10 s clip a few seconds later) -> API told
The live view (MJPEG port) is published by its own thread at LIVE_FPS from the newest camera frame, with the
boxes of the last analysed frame drawn on it, so the video stays smooth even when analysis is slow.
"""

import argparse
import logging
import sys
import threading
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
from worker.verify import verify_fire_smoke

REPORT_EVERY_S = 5.0
ZONE_REFRESH_S = 30.0
IDLE_SLEEP_S = 0.005
JPEG_QUALITY = 80
LIVE_FPS = 25  # live view frames per second (phone cameras send about 25 to 30), independent of the models
ZONE_COLOURS = {"restricted": (0, 0, 255), "ppe": (0, 165, 255), "work": (0, 255, 255)}  # BGR

log = logging.getLogger("safetyai.worker")


def helmet_camera(camera_id: str, cameras_csv: str) -> bool:
    """Does this camera run the rider-helmet model? Empty list = every camera."""
    wanted = {c.strip() for c in cameras_csv.split(",") if c.strip()}
    return not wanted or camera_id in wanted


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
    restricted = [z for z in zones if z.kind == "restricted"]
    for person in people:
        x1, y1, x2, y2 = (int(v) for v in person.xyxy)
        angle = torso_angle_deg(person.keypoints)
        angle_text = "" if angle is None else f" {angle:.0f}deg"
        name = (names or {}).get(person.track_id)
        label = f"{name}" if name else f"ID {person.track_id}{angle_text}"  # a recognised face shows its name
        colour = (0, 200, 0)
        inside = [z for z in restricted if point_in_polygon(((x1 + x2) / 2 / w, y2 / h), z.polygon)]
        if inside:  # in a restricted area: green = allowed by face, red = unknown face, no face or not allowed
            allowed = bool(name) and all(name in z.allowed for z in inside)
            colour = (0, 200, 0) if allowed else (0, 0, 255)
            label = f"{name} (allowed)" if allowed else f"{name} (NOT allowed)" if name else "unknown"
        cv2.rectangle(out, (x1, y1), (x2, y2), colour, 2)
        cv2.putText(out, label, (x1, max(y1 - 6, 12)), cv2.FONT_HERSHEY_SIMPLEX, 0.6, colour, 2)
    cv2.putText(out, fps_text, (8, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)
    return out


THING_COLOURS = {"fire": (0, 115, 249), "flame": (0, 115, 249), "smoke": (175, 163, 156)}  # BGR: orange, grey


def draw_things(image: np.ndarray, things: list[DetectionObs]) -> np.ndarray:
    """Fire, smoke and rider boxes with their score, so a test shows what the model sees (rules decide alarms)."""
    for d in things:
        x1, y1, x2, y2 = (int(v) for v in d.box)
        colour = THING_COLOURS.get(d.label) or (
            (21, 204, 250) if "helmet" in d.label and ("no" in d.label or "without" in d.label)  # safety yellow
            else (94, 197, 34) if "helmet" in d.label  # a helmet on: green
            else (21, 204, 250) if d.label in ("bald", "cap", "face and hair") else (255, 0, 255))
        cv2.rectangle(image, (x1, y1), (x2, y2), colour, 2)
        cv2.putText(image, f"{d.label} {d.confidence:.2f}", (x1, max(y1 - 6, 12)), cv2.FONT_HERSHEY_SIMPLEX, 0.6,
                    colour, 2)
    return image


class LiveView:
    """Publishes the newest camera frame LIVE_FPS times a second with the last analysis drawn on it, only while
    someone is watching the stream (drawing and JPEG-encoding 25 frames a second costs CPU the AI needs)."""

    def __init__(self, capture: LatestFrameCapture, mjpeg: MjpegServer, zones: list[ZoneDef]) -> None:
        self.capture, self.mjpeg = capture, mjpeg
        self.zones, self.people, self.names, self.fps_text = zones, [], None, "starting..."
        self.things: list[DetectionObs] = []  # fire, smoke and helmet boxes of the last analysed frame
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="live-view", daemon=True)

    def start(self) -> "LiveView":
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2)

    def _run(self) -> None:
        last_id = -1
        while not self._stop.wait(1.0 / LIVE_FPS):
            if self.mjpeg.viewers == 0:  # nobody watching this camera: no drawing or encoding (P43)
                continue
            frame = self.capture.latest()
            if frame is None or frame.frame_id == last_id:
                continue
            last_id = frame.frame_id
            try:
                image = draw_things(annotate(frame.image, self.zones, self.people, self.fps_text, self.names),
                                    self.things)
                ok, jpeg = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
                if ok:
                    self.mjpeg.publish(jpeg.tobytes())
            except Exception:  # one bad frame never stops the live view
                log.exception("live view frame failed")


def start_faces(camera_id: str, zones: list[ZoneDef]) -> "FaceWatcher | None":
    """Face recognition for a camera that has a restricted area; None when it has none (or the models are missing)."""
    if not any(z.kind == "restricted" for z in zones):
        return None
    try:
        watcher = FaceWatcher()
        watcher.bank = load_face_bank() or watcher.bank
        log.info("camera %s: face recognition on, %d fingerprints enrolled", camera_id, len(watcher.bank))
        return watcher
    except Exception:  # model download failed (offline first run): restricted zones still alert as before
        log.warning("camera %s: face recognition off (models not available)", camera_id, exc_info=True)
        return None


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
                                      min(settings.detect_conf, settings.fire_conf, settings.smoke_conf), "fire")
    # a bare head counts as "no helmet", so the helmet model runs only on the gate cameras listed in HELMET_CAMERAS
    helmet_detector = (optional_detector(settings.helmet_model_path, settings.detect_imgsz, settings.detect_conf,
                                         "rider helmet") if helmet_camera(camera.id, settings.helmet_cameras) else None)
    if helmet_detector is not None and not settings.helmet_cameras.strip():
        print(f"[{camera.id}] the rider-helmet model runs on EVERY camera: set HELMET_CAMERAS to your gate "
              "camera(s) so the other cameras detect faster", flush=True)
    for extra in (ppe_detector, fire_detector, helmet_detector):
        if extra is not None:
            extra.warm_up()

    zones = load_zones(camera.id) or []
    faces_on = start_faces(camera.id, zones) if settings.face_enabled else None
    rule_cfg = RuleConfig.from_settings(settings)
    engine = RulesEngine(zones, rule_cfg)
    fire_labels, smoke_labels = rule_cfg.fire_labels, rule_cfg.smoke_labels
    if settings.fire_frames < rule_cfg.fire_frames or settings.smoke_frames < rule_cfg.smoke_frames:
        print(f"[{camera.id}] FIRE_FRAMES/SMOKE_FRAMES below 3 raised to 3: one or two odd frames never alarm",
              flush=True)
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
    view = LiveView(capture, mjpeg, zones).start()
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
                    view.zones = zones
                if faces_on is not None:  # newly enrolled faces count within ZONE_REFRESH_S
                    faces_on.bank = load_face_bank() or faces_on.bank
                elif settings.face_enabled:  # a restricted area was just added to this camera (Restricted areas page)
                    faces_on = start_faces(camera.id, zones)
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
            timings = {"people": detect_ms}  # ms per model on this frame, printed with the fps to find a slow one
            t1 = time.perf_counter()
            if ppe_detector is not None and any(z.kind == "ppe" for z in zones):
                ppe = [DetectionObs(d.xyxy, d.label, d.confidence) for d in ppe_detector.detect(keyframe.image)]
                timings["ppe"], t1 = (time.perf_counter() - t1) * 1000, time.perf_counter()
            if fire_detector is not None:
                fire = [DetectionObs(d.xyxy, d.label, d.confidence) for d in fire_detector.detect(keyframe.image)]
                if settings.verify_fire and fire:  # P40: the box must also look like flames / smoke in the picture
                    fire = verify_fire_smoke(keyframe.image, fire, [p.xyxy for p in people], fire_labels,
                                             smoke_labels, settings.fire_colour_min, settings.smoke_colour_min)
                timings["fire"], t1 = (time.perf_counter() - t1) * 1000, time.perf_counter()
            riders = None
            if helmet_detector is not None:
                riders = [DetectionObs(d.xyxy, d.label, d.confidence) for d in helmet_detector.detect(keyframe.image)]
                timings["helmet"], t1 = (time.perf_counter() - t1) * 1000, time.perf_counter()
            names = faces_on.update(keyframe.image, people, zones) if faces_on is not None else None
            if faces_on is not None:
                timings["faces"] = (time.perf_counter() - t1) * 1000
            view.people, view.names = people, names
            view.things = (fire or []) + (riders or [])
            candidates = engine.update(now, datetime.now(timezone.utc), w, h, [to_obs(p) for p in people], bags,
                                       ppe=ppe, fire=fire, riders=riders, identities=names)

            analysed_in_window += 1
            if now - window_start >= REPORT_EVERY_S:
                analysed_fps = analysed_in_window / (now - window_start)
                live["fps"] = analysed_fps
                fps_text = f"analysed {analysed_fps:.1f} fps | camera {capture.camera_fps:.1f} fps"
                view.fps_text = fps_text
                ms = " ".join(f"{k} {v:.0f}" for k, v in timings.items())
                print(f"[{camera.id}] {fps_text} | persons={len(people)} | ms: {ms}", flush=True)
                window_start, analysed_in_window = now, 0

            if not candidates:
                continue
            # the snapshot is the analysed frame itself, so its boxes match the picture exactly
            ok, jpeg = cv2.imencode(".jpg", annotate(keyframe.image, zones, people, fps_text, names),
                                    [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
            if not ok:
                continue
            jpeg_bytes = jpeg.tobytes()
            for candidate in candidates:
                incident_id = reporter.report(candidate, jpeg_bytes, now)
                print(f"[{camera.id}] INCIDENT {incident_id} {candidate.type} ({candidate.severity}) "
                      f"zone={candidate.zone} {candidate.reason}", flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        heartbeat.stop()
        view.stop()
        capture.stop()
        if recorder is not None:
            recorder.stop()
        mjpeg.stop()
    return 0


def watch_parent(parent_pid: int, every_s: float = 2.0) -> None:
    """A worker started by the API stops when the API stops (or reloads), so no old worker keeps the camera
    with old settings after a restart."""
    import os

    import psutil

    def check() -> None:
        while True:
            time.sleep(every_s)
            if not psutil.pid_exists(parent_pid):
                log.warning("the API (pid %d) is gone: worker stopping", parent_pid)
                os._exit(0)

    threading.Thread(target=check, name="parent-watch", daemon=True).start()


def main() -> int:
    parser = argparse.ArgumentParser(description="RingGuard camera worker")
    parser.add_argument("--camera", required=True, help="camera id from config/cameras.yaml")
    parser.add_argument("--seconds", type=float, default=0, help="stop after N seconds (0 = run forever)")
    parser.add_argument("--source", help="testing only: use this video file or URL instead of the config source")
    parser.add_argument("--parent-pid", type=int, help="set by the API: stop when that process is gone")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    if args.parent_pid:
        watch_parent(args.parent_pid)
    return run(args.camera, args.seconds, args.source)


if __name__ == "__main__":
    sys.exit(main())
