"""Latest-frame capture thread.

One thread reads the camera nonstop. It overwrites a single "latest" slot with every frame,
so frames never queue up behind a slow detector. It also keeps a "keyframe" slot holding the
newest frame whose number is a multiple of `every_n` (the frames that get analysed).

The detector only ever reads from these slots. It never touches the camera directly.
Every frame is also handed to `on_frame` (the ring buffer for clips).

Recorded video files are played at their real speed and loop forever, so a file behaves like a
live camera (the roadmap uses recorded clips as live sources).
"""

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

import cv2
import numpy as np

log = logging.getLogger("safetyai.capture")

RECONNECT_DELAY_S = 5.0
FPS_WINDOW_S = 5.0
DEFAULT_FILE_FPS = 15.0


@dataclass(frozen=True)
class Frame:
    frame_id: int
    image: np.ndarray


def parse_source(source: str) -> int | str:
    """'0' means the laptop webcam index; anything else (URL or file path) is passed through."""
    return int(source) if source.isdigit() else source


def is_file_source(source: int | str) -> bool:
    return isinstance(source, str) and "://" not in source


class LatestFrameCapture:
    def __init__(self, source: str, every_n: int, on_frame: Callable[[np.ndarray], None] | None = None) -> None:
        self.source = parse_source(source)
        self.every_n = every_n
        self.on_frame = on_frame
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
        is_file = is_file_source(self.source)
        while not self._stop.is_set():
            cap = cv2.VideoCapture(self.source)
            if not cap.isOpened():
                self.online = False
                log.warning("camera source %s not available, retrying in %.0f s", self.source, RECONNECT_DELAY_S)
                self._stop.wait(RECONNECT_DELAY_S)
                continue
            self.online = True
            log.info("camera source %s opened", self.source)
            frame_interval = 1.0 / (cap.get(cv2.CAP_PROP_FPS) or DEFAULT_FILE_FPS) if is_file else 0.0
            next_due = time.monotonic()
            window_start, window_frames = time.monotonic(), 0
            while not self._stop.is_set():
                ok, image = cap.read()
                if not ok:
                    if is_file and frame_id > 0:
                        cap.set(cv2.CAP_PROP_POS_FRAMES, 0)  # end of a recorded clip: loop it
                        continue
                    break
                if frame_interval:
                    next_due += frame_interval  # play files at real speed
                    delay = next_due - time.monotonic()
                    if delay > 0:
                        self._stop.wait(delay)
                frame_id += 1
                frame = Frame(frame_id, image)
                with self._lock:
                    self._latest = frame
                    if frame_id % self.every_n == 0:
                        self._keyframe = frame
                if self.on_frame is not None:
                    self.on_frame(image)
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
