"""All-day recordings: the folder index, the API (with camera scope), clean-up, and a real FFmpeg run."""

import os
import shutil
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app
from tests.conftest import login
from worker.recorder import ContinuousRecorder, clean_old, segment_start

client = TestClient(app)
IST = ZoneInfo("Asia/Kolkata")


@pytest.fixture
def media(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "media_dir", str(tmp_path))
    return tmp_path


def make_segment(root: Path, camera: str, start_ist: datetime, seconds: int = 300) -> Path:
    path = root / "recordings" / camera / f"{start_ist:%Y-%m-%d_%H-%M-%S}.mp4"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x00" * 2048)
    end = start_ist.replace(tzinfo=IST) + timedelta(seconds=seconds)
    os.utime(path, (end.timestamp(), end.timestamp()))
    return path


def test_file_names_give_the_start_time():
    assert segment_start(Path("2026-10-26_14-35-00.mp4")) == datetime(2026, 10, 26, 14, 35)
    assert segment_start(Path("notes.mp4")) is None


def test_days_and_segments_of_a_camera(roster, media):
    make_segment(media, "cam-webcam", datetime(2026, 10, 25, 23, 55))
    make_segment(media, "cam-webcam", datetime(2026, 10, 26, 9, 0))
    make_segment(media, "cam-webcam", datetime(2026, 10, 26, 9, 5))
    headers = login(client, "1111")
    assert client.get("/recordings/days?camera_id=cam-webcam", headers=headers).json()["days"] == [
        "2026-10-26", "2026-10-25"]
    day = client.get("/recordings?camera_id=cam-webcam&date=2026-10-26", headers=headers).json()
    assert [s["start"] for s in day["segments"]] == ["2026-10-26T03:30:00Z", "2026-10-26T03:35:00Z"]  # IST -> UTC
    assert day["segments"][0]["url"] == "/media/recordings/cam-webcam/2026-10-26_09-00-00.mp4"


def test_the_recording_at_an_incident_moment(roster, media):
    make_segment(media, "cam-webcam", datetime(2026, 10, 26, 9, 0))
    headers = login(client, "1111")
    out = client.get("/recordings/at", params={"camera_id": "cam-webcam", "at": "2026-10-26T03:32:30Z"},
                     headers=headers).json()
    assert out["url"].endswith("2026-10-26_09-00-00.mp4") and out["offset_s"] == 150
    missing = client.get("/recordings/at", params={"camera_id": "cam-webcam", "at": "2026-10-26T08:00:00Z"},
                         headers=headers)
    assert missing.status_code == 404


def test_only_delegated_cameras_and_safe_ids(roster, media):
    guard = login(client, "4444")  # Suresh sees cam-webcam only
    assert client.get("/recordings/days?camera_id=cam-phone", headers=guard).status_code == 403
    assert client.get("/recordings/days?camera_id=cam-webcam", headers=guard).status_code == 200
    assert client.get("/recordings/days?camera_id=../secrets", headers=guard).status_code == 422
    assert client.get("/recordings/days?camera_id=cam-webcam").status_code == 401


def test_old_recordings_are_deleted(tmp_path):
    now = datetime(2026, 10, 26, 12, 0)
    old = make_segment(tmp_path, "cam-a", now - timedelta(days=8))
    new = make_segment(tmp_path, "cam-a", now - timedelta(hours=1))
    assert clean_old(tmp_path / "recordings", keep_days=7, min_free_gb=0, now=now) == 1
    assert not old.exists() and new.exists()


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")
def test_a_real_recording_is_written_at_a_steady_pace(tmp_path):
    rec = ContinuousRecorder("cam-x", tmp_path, fps=10, segment_s=60, keep_days=7, min_free_gb=0).start()
    t0 = time.monotonic()
    while time.monotonic() - t0 < 2.0:
        rec.push(np.zeros((480, 640, 3), dtype=np.uint8))
        time.sleep(0.01)  # a fast camera: the recorder still saves 10 fps
    rec.stop()
    files = list((tmp_path / "cam-x").glob("*.mp4"))
    assert len(files) == 1 and files[0].stat().st_size > 0 and segment_start(files[0]) is not None
    assert 15 <= rec.written <= 25
