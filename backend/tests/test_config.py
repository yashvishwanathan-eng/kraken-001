from app.config import load_cameras
from worker.capture import parse_source


def test_cameras_yaml_loads_both_cameras():
    cameras = {cam.id: cam for cam in load_cameras()}
    assert set(cameras) == {"cam-webcam", "cam-phone"}
    assert cameras["cam-webcam"].source == "0"
    assert cameras["cam-phone"].source.startswith("http://")
    assert cameras["cam-webcam"].port != cameras["cam-phone"].port


def test_parse_source_webcam_index_and_passthrough():
    assert parse_source("0") == 0
    assert parse_source("http://192.168.1.20:8080/video") == "http://192.168.1.20:8080/video"
    assert parse_source("tests/data/office.mp4") == "tests/data/office.mp4"
