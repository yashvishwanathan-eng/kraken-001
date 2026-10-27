"""P43: the worker makes the OpenVINO copy of a model once when it is missing (it is many times faster on Intel)."""

import os
import time

from worker import detectors


class FakeYOLO:
    exports = 0

    def __init__(self, path, task=None):
        self.path = path

    def export(self, format, imgsz, verbose):
        FakeYOLO.exports += 1
        from pathlib import Path
        p = Path(self.path)
        p.with_name(f"{p.stem}_openvino_model").mkdir()


def test_missing_copy_is_made_once(tmp_path, monkeypatch):
    monkeypatch.setattr(detectors, "YOLO", FakeYOLO)
    FakeYOLO.exports = 0
    weights = tmp_path / "fire.pt"
    weights.write_bytes(b"w")
    assert detectors.openvino_copy(weights, 480) == tmp_path / "fire_openvino_model"
    assert detectors.openvino_copy(weights, 480) == tmp_path / "fire_openvino_model"
    assert FakeYOLO.exports == 1
    assert not (tmp_path / ".fire.exporting").exists()


def test_a_lock_left_by_a_crashed_worker_is_ignored(tmp_path, monkeypatch):
    monkeypatch.setattr(detectors, "YOLO", FakeYOLO)
    weights = tmp_path / "pose.pt"
    weights.write_bytes(b"w")
    lock = tmp_path / ".pose.exporting"
    lock.write_text("")
    old = time.time() - detectors.EXPORT_WAIT_S - 10
    os.utime(lock, (old, old))
    assert detectors.openvino_copy(weights, 480) == tmp_path / "pose_openvino_model"
