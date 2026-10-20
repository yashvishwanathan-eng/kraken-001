import time

import cv2
import numpy as np

from worker.capture import LatestFrameCapture


def make_video(path, frames=20, fps=10):
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (64, 48))
    for i in range(frames):
        writer.write(np.full((48, 64, 3), i * 10, dtype=np.uint8))
    writer.release()


def test_latest_and_keyframe_come_from_the_video(tmp_path):
    video = tmp_path / "clip.mp4"
    make_video(video, frames=20)
    capture = LatestFrameCapture(str(video), every_n=3).start()
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            latest = capture.latest()
            if latest is not None and latest.frame_id >= 20:
                break
            time.sleep(0.02)
        latest = capture.latest()
        keyframe = capture.latest_keyframe()
        assert latest is not None and latest.frame_id >= 20  # files loop, so it may be past 20
        assert keyframe is not None
        assert keyframe.frame_id % 3 == 0
        assert keyframe.frame_id <= latest.frame_id
        assert latest.image.shape == (48, 64, 3)
    finally:
        capture.stop()


def test_file_plays_at_real_speed_and_loops(tmp_path):
    video = tmp_path / "clip.mp4"
    make_video(video, frames=10, fps=10)  # 1 second long
    seen = []
    capture = LatestFrameCapture(str(video), every_n=3, on_frame=lambda image: seen.append(time.monotonic())).start()
    try:
        time.sleep(2.5)
    finally:
        capture.stop()
    # about 10 frames per second for 2.5 s, so the 1 s clip looped at least once
    assert 18 <= len(seen) <= 32
