"""Ring buffer of recent frames, and the clip writer.

The capture thread pushes every frame in. The buffer keeps only the last `seconds` of them,
shrunk and JPEG-encoded so 12 s of video costs a few MB of memory instead of about 1 GB.
When an incident fires, the clip writer waits until `after_s` has passed, takes the frames from
`before_s` before to `after_s` after, and pipes them through FFmpeg into an H.264 MP4 that
browsers can play.
"""

import collections
import logging
import subprocess
import threading
import time
from pathlib import Path

import cv2
import numpy as np

log = logging.getLogger("safetyai.buffer")

JPEG_QUALITY = 80
FFMPEG_TIMEOUT_S = 60


class RingBuffer:
    def __init__(self, seconds: float, max_width: int) -> None:
        self.seconds = seconds
        self.max_width = max_width
        self._frames: collections.deque[tuple[float, bytes]] = collections.deque()
        self._lock = threading.Lock()

    def add(self, image: np.ndarray, t: float | None = None) -> None:
        t = time.monotonic() if t is None else t
        h, w = image.shape[:2]
        if w > self.max_width:
            image = cv2.resize(image, (self.max_width, int(h * self.max_width / w)))
        ok, jpeg = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
        if not ok:
            return
        with self._lock:
            self._frames.append((t, jpeg.tobytes()))
            while self._frames and t - self._frames[0][0] > self.seconds:
                self._frames.popleft()  # oldest frames fall off

    def between(self, start: float, end: float) -> list[tuple[float, bytes]]:
        with self._lock:
            return [(t, jpg) for t, jpg in self._frames if start <= t <= end]

    def __len__(self) -> int:
        with self._lock:
            return len(self._frames)


def write_clip(frames: list[tuple[float, bytes]], out_path: Path) -> bool:
    """Encode buffered JPEG frames to H.264 MP4 (yuv420p + faststart so browsers play it)."""
    if len(frames) < 2:
        log.warning("clip %s skipped: only %d frames", out_path.name, len(frames))
        return False
    duration = frames[-1][0] - frames[0][0]
    fps = max(1.0, (len(frames) - 1) / duration) if duration > 0 else 10.0
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "image2pipe", "-framerate", f"{fps:.2f}", "-c:v", "mjpeg", "-i", "-",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
        # libx264 needs even width and height
        "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2",
        str(out_path),
    ]
    try:
        subprocess.run(cmd, input=b"".join(jpg for _, jpg in frames), check=True,
                       capture_output=True, timeout=FFMPEG_TIMEOUT_S)
    except FileNotFoundError:
        log.error("ffmpeg not found on PATH; clip %s not written", out_path.name)
        return False
    except subprocess.CalledProcessError as exc:
        log.error("ffmpeg failed for %s: %s", out_path.name, exc.stderr.decode(errors="replace")[-300:])
        return False
    except subprocess.TimeoutExpired:
        log.error("ffmpeg timed out for %s", out_path.name)
        return False
    return True
