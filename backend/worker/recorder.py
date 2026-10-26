"""Continuous recording: every camera is saved all day, in 5-minute MP4 files.

  media/recordings/<camera_id>/2026-10-26_14-35-00.mp4   (start time in the laptop's local time)

How it stays safe when the computer is busy:
  - The capture thread only hands over its newest frame (push() just replaces one slot, never waits).
  - A separate writer thread takes that slot at a steady RECORD_FPS and feeds FFmpeg. If detection or the
    server is slow, nothing here waits for them: the recording keeps its real-time pace.
  - FFmpeg encodes H.264 (veryfast) and cuts a new file every RECORD_SEGMENT_S on the clock. Files are
    fragmented MP4, so even the file being written right now, or one cut short by a crash, still plays.
  - If FFmpeg stops, it is started again 5 s later; a camera that drops out leaves a gap, not a broken file.
  - Old files are deleted after RECORD_DAYS days, and sooner (oldest first) if free disk space falls under
    RECORD_MIN_FREE_GB, so the laptop never fills up.
"""

import logging
import shutil
import subprocess
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

import cv2
import numpy as np

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)  # Windows: ffmpeg runs without a console window

log = logging.getLogger("safetyai.recorder")

NAME_FORMAT = "%Y-%m-%d_%H-%M-%S"
RESTART_DELAY_S = 5.0
CLEAN_EVERY_S = 600.0


def segment_start(path: Path) -> datetime | None:
    """2026-10-26_14-35-00.mp4 -> its start time (naive, the laptop's local time)."""
    try:
        return datetime.strptime(path.stem, NAME_FORMAT)
    except ValueError:
        return None


def ffmpeg_command(width: int, height: int, fps: float, segment_s: int, crf: int, out_dir: Path) -> list[str]:
    return [
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{width}x{height}", "-r", f"{fps:g}", "-i", "pipe:0",
        "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", str(crf), "-pix_fmt", "yuv420p",
        "-g", str(int(fps * 2)),  # a keyframe every 2 s, so the player can jump anywhere quickly
        "-f", "segment", "-segment_time", str(segment_s), "-segment_atclocktime", "1", "-reset_timestamps", "1",
        "-strftime", "1", "-segment_format", "mp4",
        "-segment_format_options", "movflags=+frag_keyframe+empty_moov+default_base_moof",
        str(out_dir / f"{NAME_FORMAT}.mp4"),
    ]


def clean_old(root: Path, keep_days: float, min_free_gb: float, now: datetime | None = None) -> int:
    """Delete recordings older than keep_days, then the oldest ones while free disk is under min_free_gb."""
    now = now or datetime.now()
    files = sorted((f for f in root.glob("*/*.mp4") if segment_start(f)), key=lambda f: segment_start(f))
    removed = 0
    for f in files:
        if now - segment_start(f) > timedelta(days=keep_days):
            f.unlink(missing_ok=True)
            removed += 1
    files = [f for f in files if f.exists()]
    while files[:-1] and shutil.disk_usage(root).free < min_free_gb * 1024 ** 3:
        files.pop(0).unlink(missing_ok=True)  # never the newest file, which may still be written
        removed += 1
    if removed:
        log.info("recordings: removed %d old file(s)", removed)
    return removed


class ContinuousRecorder:
    def __init__(self, camera_id: str, root: Path, fps: float = 10.0, segment_s: int = 300, max_width: int = 960,
                 crf: int = 28, keep_days: float = 7, min_free_gb: float = 10) -> None:
        self.out_dir = root / camera_id
        self.root = root
        self.fps, self.segment_s, self.max_width, self.crf = fps, segment_s, max_width, crf
        self.keep_days, self.min_free_gb = keep_days, min_free_gb
        self._slot: np.ndarray | None = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="recorder", daemon=True)
        self._proc: subprocess.Popen | None = None
        self._size: tuple[int, int] | None = None
        self.written = 0

    def start(self) -> "ContinuousRecorder":
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self._thread.start()
        return self

    def push(self, image: np.ndarray) -> None:
        """Called by the capture thread for every frame. Never blocks: it only replaces the newest frame."""
        with self._lock:
            self._slot = image

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=5)
        self._close()

    def _frame_size(self, image: np.ndarray) -> tuple[int, int]:
        h, w = image.shape[:2]
        if w > self.max_width:
            h, w = int(h * self.max_width / w), self.max_width
        return w - w % 2, h - h % 2  # H.264 needs even sizes

    def _open(self, size: tuple[int, int]) -> bool:
        try:
            self._proc = subprocess.Popen(
                ffmpeg_command(size[0], size[1], self.fps, self.segment_s, self.crf, self.out_dir),
                stdin=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=NO_WINDOW)
        except FileNotFoundError:
            log.error("ffmpeg not found on PATH: continuous recording is off")
            self._stop.set()
            return False
        self._size = size
        log.info("recording %s at %dx%d, %g fps, %d s files", self.out_dir.name, size[0], size[1], self.fps,
                 self.segment_s)
        return True

    def _close(self) -> None:
        if self._proc is not None:
            try:
                self._proc.stdin.close()
                self._proc.wait(timeout=10)
            except Exception:
                self._proc.kill()
            self._proc = None

    def _run(self) -> None:
        interval = 1.0 / self.fps
        next_due = time.monotonic()
        last_clean = 0.0
        while not self._stop.is_set():
            now = time.monotonic()
            if now - last_clean >= CLEAN_EVERY_S:
                try:
                    clean_old(self.root, self.keep_days, self.min_free_gb)
                except OSError as exc:
                    log.warning("recordings clean-up failed: %s", exc)
                last_clean = now
            with self._lock:
                image = self._slot
            if image is None:  # camera not online yet (or lost): wait, leave a gap
                self._stop.wait(0.5)
                next_due = time.monotonic()
                continue
            size = self._frame_size(image)
            if self._proc is None or self._proc.poll() is not None or size != self._size:
                if self._proc is not None and self._proc.poll() is not None:
                    err = self._proc.stderr.read().decode(errors="replace")[-300:] if self._proc.stderr else ""
                    log.warning("ffmpeg stopped (%s); restarting in %.0f s", err.strip(), RESTART_DELAY_S)
                    self._proc = None
                    self._stop.wait(RESTART_DELAY_S)
                    continue
                self._close()
                if not self._open(size):
                    return
            frame = image if image.shape[1::-1] == size else cv2.resize(image, size)
            try:
                self._proc.stdin.write(frame.tobytes())
                self.written += 1
            except (BrokenPipeError, OSError):
                continue  # ffmpeg died: restarted on the next round
            next_due += interval
            delay = next_due - time.monotonic()
            if delay > 0:
                self._stop.wait(delay)
            elif delay < -1.0:
                next_due = time.monotonic()  # fell far behind (very busy laptop): skip ahead, keep real time
