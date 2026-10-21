"""Person detection, tracking and pose with Ultralytics YOLO11n-pose (built-in ByteTrack).

One model gives, per person: a box, a track ID and 17 body keypoints. Models load once per
worker process, and one warm-up frame runs before the first real frame.
"""

import logging
import math
import shutil
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from ultralytics import YOLO

from app.config import BACKEND_DIR, get_settings

log = logging.getLogger("safetyai.detectors")

PERSON_CLASS_ID = 0  # COCO class 0 = person
# COCO keypoint indexes used for the torso
LEFT_SHOULDER, RIGHT_SHOULDER, LEFT_HIP, RIGHT_HIP = 5, 6, 11, 12
MIN_KEYPOINT_CONF = 0.5


@dataclass
class Person:
    track_id: int
    xyxy: tuple[float, float, float, float]
    confidence: float
    keypoints: list[tuple[float, float, float]]  # (x, y, conf) for each of 17 points


def torso_angle_deg(keypoints: list[tuple[float, float, float]]) -> float | None:
    """Angle of the shoulder-to-hip line from vertical: 0 = upright, 90 = lying flat.

    Returns None when a shoulder or hip is not clearly visible.
    """
    needed = [keypoints[i] for i in (LEFT_SHOULDER, RIGHT_SHOULDER, LEFT_HIP, RIGHT_HIP)]
    if any(conf < MIN_KEYPOINT_CONF for _, _, conf in needed):
        return None
    shoulder_x = (keypoints[LEFT_SHOULDER][0] + keypoints[RIGHT_SHOULDER][0]) / 2
    shoulder_y = (keypoints[LEFT_SHOULDER][1] + keypoints[RIGHT_SHOULDER][1]) / 2
    hip_x = (keypoints[LEFT_HIP][0] + keypoints[RIGHT_HIP][0]) / 2
    hip_y = (keypoints[LEFT_HIP][1] + keypoints[RIGHT_HIP][1]) / 2
    # Image y grows downward, so only the sideways and vertical parts of the vector matter.
    return math.degrees(math.atan2(abs(hip_x - shoulder_x), abs(hip_y - shoulder_y)))


def hip_y(keypoints: list[tuple[float, float, float]]) -> float | None:
    """Height of the hip midpoint in pixels (bigger = lower in the image). None if hips are hidden."""
    left, right = keypoints[LEFT_HIP], keypoints[RIGHT_HIP]
    if left[2] < MIN_KEYPOINT_CONF or right[2] < MIN_KEYPOINT_CONF:
        return None
    return (left[1] + right[1]) / 2


def ensure_model_file(model_path: str) -> Path:
    """Return the weights path, downloading the official file once if it is missing."""
    path = Path(model_path)
    if not path.is_absolute():
        path = BACKEND_DIR / path
    if path.exists():
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    log.info("model weights missing, downloading to %s", path)
    YOLO(path.name)  # Ultralytics fetches the official asset into the working directory
    shutil.move(Path(path.name).resolve(), path)
    return path


def load_model(model_path: str, task: str) -> YOLO:
    """Load weights once. Prefer the OpenVINO export next to them if it exists (same results, faster on Intel).

    Make the export with:  yolo export model=models/yolo11n-pose.pt format=openvino imgsz=480
    """
    weights = ensure_model_file(model_path)
    openvino_dir = weights.with_name(f"{weights.stem}_openvino_model")
    if get_settings().prefer_openvino and openvino_dir.is_dir():
        log.info("using OpenVINO model %s", openvino_dir.name)
        return YOLO(str(openvino_dir), task=task)
    log.info("using PyTorch model %s (export to OpenVINO for about 2x speed)", weights.name)
    return YOLO(str(weights), task=task)


class PersonPoseTracker:
    def __init__(self, model_path: str, imgsz: int, conf: float) -> None:
        self.imgsz = imgsz
        self.conf = conf
        self.model = load_model(model_path, task="pose")

    def warm_up(self) -> None:
        blank = np.zeros((self.imgsz, self.imgsz, 3), dtype=np.uint8)
        self.track(blank)
        log.info("detector warm-up done")

    def track(self, frame: np.ndarray) -> list[Person]:
        result = self.model.track(
            frame,
            classes=[PERSON_CLASS_ID],
            persist=True,
            tracker="bytetrack.yaml",
            imgsz=self.imgsz,
            conf=self.conf,
            verbose=False,
        )[0]
        boxes = result.boxes
        if boxes is None or boxes.id is None or result.keypoints is None:
            return []  # no tracked people in this frame (or tracker still warming up)
        ids = boxes.id.int().tolist()
        coords = boxes.xyxy.tolist()
        scores = boxes.conf.tolist()
        xy = result.keypoints.xy.tolist()
        kp_conf = result.keypoints.conf.tolist()
        people = []
        for i, box, score, points, confs in zip(ids, coords, scores, xy, kp_conf):
            keypoints = [(x, y, c) for (x, y), c in zip(points, confs)]
            people.append(Person(track_id=i, xyxy=tuple(box), confidence=score, keypoints=keypoints))
        return people


# COCO class ids for the unattended-bag rule
BAG_CLASSES = {24: "backpack", 26: "handbag", 28: "suitcase"}


@dataclass
class Bag:
    xyxy: tuple[float, float, float, float]
    label: str
    confidence: float


class BagDetector:
    """Plain YOLO11n detector limited to bag classes. Run about once a second: bags barely move."""

    def __init__(self, model_path: str, imgsz: int, conf: float) -> None:
        self.imgsz = imgsz
        self.conf = conf
        self.model = load_model(model_path, task="detect")

    def warm_up(self) -> None:
        self.detect(np.zeros((self.imgsz, self.imgsz, 3), dtype=np.uint8))

    def detect(self, frame: np.ndarray) -> list[Bag]:
        boxes = self.model.predict(frame, classes=list(BAG_CLASSES), imgsz=self.imgsz, conf=self.conf,
                                   verbose=False)[0].boxes
        if boxes is None:
            return []
        return [
            Bag(xyxy=tuple(box), label=BAG_CLASSES[int(cls)], confidence=score)
            for box, cls, score in zip(boxes.xyxy.tolist(), boxes.cls.tolist(), boxes.conf.tolist())
        ]


@dataclass
class Detection:
    xyxy: tuple[float, float, float, float]
    label: str  # lower-cased class name from the model
    confidence: float


class ClassDetector:
    """A custom YOLO model (PPE or fire/smoke). Class names come from the model file itself."""

    def __init__(self, model_path: str, imgsz: int, conf: float) -> None:
        self.imgsz = imgsz
        self.conf = conf
        self.model = load_model(model_path, task="detect")
        self.names = {int(k): str(v).lower() for k, v in self.model.names.items()}
        log.info("custom model %s classes: %s", Path(model_path).name, ", ".join(self.names.values()))

    def warm_up(self) -> None:
        self.detect(np.zeros((self.imgsz, self.imgsz, 3), dtype=np.uint8))

    def detect(self, frame: np.ndarray) -> list[Detection]:
        boxes = self.model.predict(frame, imgsz=self.imgsz, conf=self.conf, verbose=False)[0].boxes
        if boxes is None:
            return []
        return [Detection(tuple(box), self.names.get(int(cls), str(int(cls))), score)
                for box, cls, score in zip(boxes.xyxy.tolist(), boxes.cls.tolist(), boxes.conf.tolist())]


def optional_detector(model_path: str, imgsz: int, conf: float, purpose: str) -> ClassDetector | None:
    """Load a custom model only if its path is set and the file exists; otherwise that rule stays off."""
    if not model_path:
        log.info("%s rule off: no model path set", purpose)
        return None
    path = Path(model_path) if Path(model_path).is_absolute() else BACKEND_DIR / model_path
    if not path.exists():
        log.warning("%s rule off: model file %s not found", purpose, path)
        return None
    return ClassDetector(str(path), imgsz, conf)
