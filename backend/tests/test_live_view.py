import time

import numpy as np

from worker.capture import Frame
from worker.run_worker import LiveView


class FakeCapture:
    def __init__(self):
        self.n = 0

    def latest(self):
        self.n += 1  # a new camera frame every time it is asked, like a live camera
        return Frame(frame_id=self.n, image=np.zeros((48, 64, 3), np.uint8))


class FakeMjpeg:
    def __init__(self):
        self.frames = []

    def publish(self, jpeg):
        self.frames.append(jpeg)


def test_live_view_keeps_publishing_while_analysis_is_slow():
    mjpeg = FakeMjpeg()
    view = LiveView(FakeCapture(), mjpeg, zones=[]).start()
    time.sleep(0.6)  # no analysis happens at all in this time
    view.stop()
    assert len(mjpeg.frames) >= 4  # about LIVE_FPS frames a second, not one per analysed frame
    assert mjpeg.frames[0][:2] == b"\xff\xd8"  # JPEG
