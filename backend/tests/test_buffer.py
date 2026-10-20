import shutil
import subprocess

import numpy as np
import pytest

from worker.buffer import RingBuffer, write_clip


def frame(value=100, w=1280, h=720):
    return np.full((h, w, 3), value, dtype=np.uint8)


def test_ring_buffer_keeps_only_the_last_seconds():
    ring = RingBuffer(seconds=3, max_width=640)
    for i in range(100):
        ring.add(frame(i), t=i * 0.1)  # 10 s of frames at 10 fps
    kept = ring.between(0, 100)
    assert kept[0][0] >= 9.9 - 3 - 1e-9 and kept[-1][0] == pytest.approx(9.9)
    assert 29 <= len(ring) <= 31


def test_between_returns_the_clip_window():
    ring = RingBuffer(seconds=12, max_width=640)
    for i in range(100):
        ring.add(frame(i), t=i * 0.1)
    window = ring.between(4.0, 6.0)
    assert window[0][0] == pytest.approx(4.0) and window[-1][0] == pytest.approx(6.0)


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_write_clip_makes_browser_playable_h264(tmp_path):
    ring = RingBuffer(seconds=12, max_width=640)
    for i in range(50):
        ring.add(frame(i * 4), t=i * 0.1)
    out = tmp_path / "1.mp4"
    assert write_clip(ring.between(0, 10), out)
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=codec_name,pix_fmt",
         "-of", "default=noprint_wrappers=1", str(out)], capture_output=True, text=True, check=True).stdout
    fields = dict(line.split("=", 1) for line in probe.strip().splitlines())
    assert fields == {"codec_name": "h264", "pix_fmt": "yuv420p"}
