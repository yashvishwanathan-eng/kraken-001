"""Restricted areas page (P35): make a whole camera restricted, pick who may enter, undo it."""

from fastapi.testclient import TestClient

from app.main import app
from app.models import Camera, User, Zone
from tests.conftest import login
from worker import run_worker
from worker.rules import ZoneDef

client = TestClient(app)


def test_restrict_a_camera_pick_people_and_undo(roster):
    admin = login(client, "1111")
    camera = roster.query(Camera).order_by(Camera.id).first()
    made = client.post("/restricted", json={"camera_id": camera.id}, headers=admin)
    assert made.status_code == 201 and made.json()["whole_camera"] is True
    zone_id = made.json()["zone_id"]
    again = client.post("/restricted", json={"camera_id": camera.id}, headers=admin).json()
    assert again["zone_id"] == zone_id  # pressing twice never makes two areas

    anita = roster.query(User).filter_by(name="Anita Rao").one()
    out = client.put(f"/restricted/{zone_id}/people", json={"user_ids": [anita.id]}, headers=admin).json()
    assert out["allowed_user_ids"] == [anita.id]
    assert client.put(f"/restricted/{zone_id}/people", json={"user_ids": [99999]}, headers=admin).status_code == 422

    page = client.get("/restricted", headers=admin).json()
    cam = next(c for c in page["cameras"] if c["id"] == camera.id)
    assert any(a["zone_id"] == zone_id and a["allowed_user_ids"] == [anita.id] for a in cam["areas"])
    assert next(p for p in page["people"] if p["id"] == anita.id)["face_samples"] == 0

    assert client.delete(f"/restricted/{zone_id}", headers=admin).status_code == 204
    roster.expire_all()
    assert roster.get(Zone, zone_id) is None


def test_bad_input_and_guards(roster):
    admin = login(client, "1111")
    assert client.post("/restricted", json={"camera_id": "no-such-camera"}, headers=admin).status_code == 422
    assert client.post("/restricted", json={}, headers=admin).status_code == 422
    assert client.delete("/restricted/99999", headers=admin).status_code == 404
    other = roster.query(Zone).filter(Zone.kind != "restricted").first()
    assert client.put(f"/restricted/{other.id}/people", json={"user_ids": []}, headers=admin).status_code == 404
    assert client.get("/restricted", headers=login(client, "4444")).status_code == 403  # guards cannot


def test_face_recognition_starts_only_with_a_restricted_area(monkeypatch):
    monkeypatch.setattr(run_worker, "FaceWatcher", lambda: type("W", (), {"bank": []})())
    monkeypatch.setattr(run_worker, "load_face_bank", lambda: None)
    work = ZoneDef("floor", "work", [(0, 0), (1, 0), (1, 1)], None)
    room = ZoneDef("room", "restricted", [(0, 0), (1, 0), (1, 1)], None)
    assert run_worker.start_faces("cam", [work]) is None
    assert run_worker.start_faces("cam", [work, room]) is not None


def test_live_view_colours_allowed_green_and_others_red():
    import numpy as np

    from worker.detectors import Person

    room = ZoneDef("room", "restricted", [(0, 0), (1, 0), (1, 1), (0, 1)], None, frozenset({"Yash"}))
    no_pose = [(0.0, 0.0, 0.0)] * 17
    yash = Person(1, (10, 10, 40, 90), 0.9, no_pose)
    stranger = Person(2, (60, 10, 90, 90), 0.9, no_pose)
    image = run_worker.annotate(np.zeros((100, 100, 3), np.uint8), [room], [yash, stranger], "", {1: "Yash", 2: ""})
    assert tuple(image[50, 10]) == (0, 200, 0)  # left edge of Yash's box: green
    assert tuple(image[50, 60]) == (0, 0, 255)  # left edge of the stranger's box: red
