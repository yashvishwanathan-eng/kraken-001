"""Face recognition on the laptop (P34): who is this person in the restricted zone?

Two small OpenCV models, no training:
  YuNet  finds faces in a picture (where the face is, and the eyes, nose and mouth corners)
  SFace  turns one face into a "fingerprint" of 128 numbers; two photos of the same person give fingerprints
         that point the same way (cosine similarity about 0.6 to 0.9), two different people about 0 to 0.3

Enrollment keeps only the fingerprints (FaceSample rows), never the photos. Recognition compares a face seen
by a camera with every enrolled fingerprint and takes the best; at or above FACE_MATCH (0.40) it is that person.
The model files (about 39 MB) are downloaded once into models/ from the OpenCV model zoo.
"""

import logging
import threading
import urllib.request
from dataclasses import dataclass

import cv2
import numpy as np

from app.config import BACKEND_DIR, get_settings

log = logging.getLogger("safetyai.faces")

ZOO = "https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models"
DETECTOR = ("face_detection_yunet_2023mar.onnx", f"{ZOO}/face_detection_yunet/face_detection_yunet_2023mar.onnx")
RECOGNIZER = ("face_recognition_sface_2021dec.onnx",
              f"{ZOO}/face_recognition_sface/face_recognition_sface_2021dec.onnx")
MIN_FACE_PX = 24  # smaller faces are too blurry to recognise (CCTV faces are often 30 to 60 px)
ENROLL_FACE_PX = 60  # enrollment photos must be closer, so the stored fingerprints are good


class FaceError(Exception):
    """A photo that cannot be used for enrollment (no face, two faces, too small)."""


def _model(name: str, url: str) -> str:
    path = BACKEND_DIR / "models" / name
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        log.info("downloading face model %s (first use only)", name)
        tmp = path.with_suffix(".part")
        urllib.request.urlretrieve(url, tmp)
        tmp.replace(path)
    return str(path)


@dataclass
class Face:
    box: tuple[int, int, int, int]  # x, y, w, h in the picture given
    score: float
    embedding: np.ndarray  # 128 float32, unit length


class FaceEngine:
    """One per process. OpenCV's models are not thread-safe, so every call takes the lock."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._detector = cv2.FaceDetectorYN.create(_model(*DETECTOR), "", (320, 320), 0.8)
        self._recognizer = cv2.FaceRecognizerSF.create(_model(*RECOGNIZER), "")

    def faces(self, image: np.ndarray) -> list[Face]:
        """Every face in the picture, biggest first, with its fingerprint."""
        h, w = image.shape[:2]
        with self._lock:
            self._detector.setInputSize((w, h))
            _, found = self._detector.detect(image)
            out = []
            for row in ([] if found is None else found):
                x, y, fw, fh = (int(v) for v in row[:4])
                if min(fw, fh) < MIN_FACE_PX:
                    continue
                aligned = self._recognizer.alignCrop(image, row)
                vec = self._recognizer.feature(aligned).flatten().astype(np.float32)
                out.append(Face((x, y, fw, fh), float(row[14]), vec / (np.linalg.norm(vec) or 1.0)))
        return sorted(out, key=lambda f: f.box[2] * f.box[3], reverse=True)

    def enroll(self, image: np.ndarray) -> np.ndarray:
        """The fingerprint of the one person in an enrollment photo. Raises FaceError if it cannot be used."""
        found = self.faces(image)
        if not found:
            raise FaceError("no face found: look at the camera, in good light")
        if len(found) > 1 and found[1].box[2] >= found[0].box[2] * 0.6:
            raise FaceError("more than one face: only the person being enrolled should be in the picture")
        if min(found[0].box[2:]) < ENROLL_FACE_PX:
            raise FaceError("face too small: come closer to the camera")
        return found[0].embedding


_engine: FaceEngine | None = None
_engine_lock = threading.Lock()


def engine() -> FaceEngine:
    global _engine
    with _engine_lock:
        if _engine is None:
            _engine = FaceEngine()
        return _engine


def decode(data: bytes) -> np.ndarray:
    image = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise FaceError("not a picture (send JPEG or PNG)")
    return image


def to_bytes(embedding: np.ndarray) -> bytes:
    return np.asarray(embedding, dtype=np.float32).tobytes()


def from_bytes(data: bytes) -> np.ndarray:
    return np.frombuffer(data, dtype=np.float32)


class FaceBank:
    """The enrolled people: names and their fingerprints, matched with one matrix product."""

    def __init__(self, samples: list[tuple[str, np.ndarray]]) -> None:
        self.names = [name for name, _ in samples]
        self.matrix = np.stack([vec for _, vec in samples]) if samples else np.zeros((0, 128), np.float32)

    def __len__(self) -> int:
        return len(self.names)

    def match(self, embedding: np.ndarray, threshold: float | None = None) -> tuple[str | None, float]:
        """(name, similarity) of the best enrolled person, or (None, best) when nobody is close enough."""
        if not self.names:
            return None, 0.0
        sims = self.matrix @ embedding
        best = int(np.argmax(sims))
        limit = get_settings().face_match if threshold is None else threshold
        return (self.names[best] if sims[best] >= limit else None), float(sims[best])
