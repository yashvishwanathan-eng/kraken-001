"""Person detection and tracking with Ultralytics YOLO (built-in ByteTrack).

Models load once per worker process. One warm-up frame runs before the first real frame.
"""

import logging
import shutil
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from ultralytics import YOLO

from app.config import BACKEND_DIR

log = logging.getLogger("safetyai.detectors")

PERSON_CLASS_ID = 0  # COCO class 0 = person


@dataclass
class Track:
    track_id: int
    xyxy: tuple[float, float, float, float]
    confidence: float


def ensure_model_file(model_path: str) -> Path:
    """Return the weights path, downloading yolo11n.pt once if it is missing."""
    path = Path(model_path)
    if not path.is_absolute():
        path = BACKEND_DIR / path
    if path.exists():
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    log.info("model weights missing, downloading to %s", path)
    downloaded = YOLO(path.name)  # Ultralytics fetches the official asset into the working directory
    shutil.move(Path(path.name).resolve(), path)
    del downloaded
    return path


class PersonTracker:
    def __init__(self, model_path: str, imgsz: int, conf: float) -> None:
        self.imgsz = imgsz
        self.conf = conf
        self.model = YOLO(str(ensure_model_file(model_path)))

    def warm_up(self) -> None:
        blank = np.zeros((self.imgsz, self.imgsz, 3), dtype=np.uint8)
        self.track(blank)
        log.info("detector warm-up done")

    def track(self, frame: np.ndarray) -> list[Track]:
        results = self.model.track(
            frame,
            classes=[PERSON_CLASS_ID],
            persist=True,
            tracker="bytetrack.yaml",
            imgsz=self.imgsz,
            conf=self.conf,
            verbose=False,
        )
        boxes = results[0].boxes
        if boxes is None or boxes.id is None:
            return []  # tracker has no IDs yet (first frames)
        ids = boxes.id.int().tolist()
        coords = boxes.xyxy.tolist()
        scores = boxes.conf.tolist()
        return [Track(track_id=i, xyxy=tuple(c), confidence=s) for i, c, s in zip(ids, coords, scores)]
