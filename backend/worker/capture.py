"""Latest-frame capture thread.

One thread reads the camera nonstop. It overwrites a single "latest" slot with every frame,
so frames never queue up behind a slow detector. It also keeps a "keyframe" slot holding the
newest frame whose number is a multiple of `every_n` (the frames that get analysed).

The detector only ever reads from these slots. It never touches the camera directly.
"""

import logging
import threading
import time
from dataclasses import dataclass

import cv2
import numpy as np

log = logging.getLogger("safetyai.capture")

RECONNECT_DELAY_S = 5.0
FPS_WINDOW_S = 5.0


@dataclass(frozen=True)
class Frame:
    frame_id: int
    image: np.ndarray


def parse_source(source: str) -> int | str:
    """'0' means the laptop webcam index; anything else (URL or file path) is passed through."""
    return int(source) if source.isdigit() else source


class LatestFrameCapture:
    def __init__(self, source: str, every_n: int) -> None:
        self.source = parse_source(source)
        self.every_n = every_n
        self._lock = threading.Lock()
        self._latest: Frame | None = None
        self._keyframe: Frame | None = None
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="capture", daemon=True)
        self.online = False
        self.camera_fps = 0.0

    def start(self) -> "LatestFrameCapture":
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2)

    def latest(self) -> Frame | None:
        with self._lock:
            return self._latest

    def latest_keyframe(self) -> Frame | None:
        with self._lock:
            return self._keyframe

    def _run(self) -> None:
        frame_id = 0
        while not self._stop.is_set():
            cap = cv2.VideoCapture(self.source)
            if not cap.isOpened():
                self.online = False
                log.warning("camera source %s not available, retrying in %.0f s", self.source, RECONNECT_DELAY_S)
                self._stop.wait(RECONNECT_DELAY_S)
                continue
            self.online = True
            log.info("camera source %s opened", self.source)
            window_start, window_frames = time.monotonic(), 0
            while not self._stop.is_set():
                ok, image = cap.read()
                if not ok:
                    break
                frame_id += 1
                frame = Frame(frame_id, image)
                with self._lock:
                    self._latest = frame
                    if frame_id % self.every_n == 0:
                        self._keyframe = frame
                window_frames += 1
                now = time.monotonic()
                if now - window_start >= FPS_WINDOW_S:
                    self.camera_fps = window_frames / (now - window_start)
                    window_start, window_frames = now, 0
            cap.release()
            self.online = False
            if not self._stop.is_set():
                log.warning("camera source %s lost, reconnecting in %.0f s", self.source, RECONNECT_DELAY_S)
                self._stop.wait(RECONNECT_DELAY_S)
