"""The real demo config (config/*.yaml) is edited by hand before the event; catch mistakes here.

The other tests use the frozen copies in tests/fixtures/, so these are the only tests that read config/.
"""

import yaml

from app.config import BACKEND_DIR, load_cameras
from app.models import Zone
from app.seed import seed_cameras, seed_zones
from app.services.permissions import ACTIONS
from tests.conftest import FIXTURES
from worker.rules import point_in_polygon

CONFIG = BACKEND_DIR / "config"


def read(name: str) -> dict:
    return yaml.safe_load((CONFIG / name).read_text(encoding="utf-8")) or {}


def test_demo_cameras_have_unique_ids_and_ports():
    cameras = load_cameras(str(CONFIG / "cameras.yaml"))
    ids = [c.id for c in cameras]
    ports = [c.port for c in cameras]
    assert {"cam-door", "cam-floor", "cam-entrance"} <= set(ids)
    assert len(ids) == len(set(ids)) and len(ports) == len(set(ports))


def test_demo_zones_point_at_real_cameras_and_stay_inside_the_frame():
    camera_ids = {c.id for c in load_cameras(str(CONFIG / "cameras.yaml"))}
    zones = read("zones.yaml")["zones"]
    for camera_id, items in zones.items():
        assert camera_id in camera_ids, f"zones.yaml names unknown camera {camera_id}"
        names = [z["name"] for z in items]
        assert len(names) == len(set(names)), f"duplicate zone name on {camera_id}"
        for zone in items:
            assert zone["kind"] in {"restricted", "ppe", "work"}, zone["name"]
            assert len(zone["polygon"]) >= 3, zone["name"]
            assert all(0 <= x <= 1 and 0 <= y <= 1 for x, y in zone["polygon"]), zone["name"]


def test_demo_layout_matches_the_plan():
    zones = {cam: {z["name"]: z for z in items} for cam, items in read("zones.yaml")["zones"].items()}
    door = zones["cam-door"]["server-room-door"]
    assert door["kind"] == "restricted"
    assert point_in_polygon((0.5, 0.9), [tuple(p) for p in door["polygon"]])  # someone standing at the door
    assert zones["cam-entrance"]["entrance"]["max_people"] >= 2


def test_demo_users_and_responders_are_consistent():
    camera_ids = {c.id for c in load_cameras(str(CONFIG / "cameras.yaml"))}
    users = read("demo_users.yaml")["users"]
    for user in users:
        assert set(user["cameras"]) <= camera_ids, user["key"]
        assert set(user["actions"]) <= ACTIONS, user["key"]
    keys = {u["key"] for u in users}
    for responder in read("demo_responders.yaml")["responders"]:
        assert responder["user"] in keys, responder["name"]


def test_seed_removes_zones_dropped_from_the_file(db, tmp_path):
    seed_cameras(db)
    db.flush()
    seed_zones(db, FIXTURES / "zones.yaml")
    db.commit()
    smaller = tmp_path / "zones.yaml"
    smaller.write_text(yaml.safe_dump({"zones": {"cam-webcam": [
        {"name": "server-room-door", "kind": "restricted", "polygon": [[0, 0], [1, 0], [1, 1]]}]}}))
    seed_zones(db, smaller)
    db.commit()
    names = {(z.camera_id, z.name) for z in db.query(Zone)}
    assert ("cam-webcam", "assembly-line") not in names
    assert ("cam-webcam", "server-room-door") in names
    assert ("cam-phone", "loading-bay") in names  # cameras not in the file are left alone
