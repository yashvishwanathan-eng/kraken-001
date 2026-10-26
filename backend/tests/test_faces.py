"""Face recognition for restricted zones (P34): the rule, the matching, the API and the camera-side watcher.

The real OpenCV models are tested only when FACE_TEST_DIR points at a folder with two photos of one person
(a1.jpg, a2.jpg) and one of another (b1.jpg); nothing with faces is kept in the repo."""

import os
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models import FaceSample, Zone
from app.services import faces
from tests.conftest import login
from worker.rules import PersonObs, RuleConfig, RulesEngine, ZoneDef

client = TestClient(app)
NOW = datetime(2026, 10, 27, 10, 0, tzinfo=timezone.utc)
ROOM = ZoneDef("server-room", "restricted", [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)], None,
               frozenset({"Anita Rao"}))
PERSON = PersonObs(7, (100, 50, 200, 400), 0.9, None, None)


def run_rule(identities):
    engine = RulesEngine([ROOM], RuleConfig(access_dwell_s=3))
    found = []
    for t in range(5):
        found += engine.update(float(t), NOW, 640, 480, [PERSON], identities=identities)
    return [c for c in found if c.type == "access"]


def test_an_allowed_face_enters_without_an_alert():
    assert run_rule({7: "Anita Rao"}) == []


def test_an_unknown_face_or_no_face_or_someone_else_is_an_alert():
    assert "unknown face" in run_rule({7: ""})[0].reason
    assert "face not visible" in run_rule({})[0].reason
    assert "Vikram Shah, not allowed here" in run_rule({7: "Vikram Shah"})[0].reason


def unit(v):
    v = np.asarray(v, dtype=np.float32)
    return v / np.linalg.norm(v)


def test_the_bank_picks_the_closest_enrolled_person():
    a, b = unit(np.r_[1.0, np.zeros(127)]), unit(np.r_[0.0, 1.0, np.zeros(126)])
    bank = faces.FaceBank([("Anita Rao", a), ("Vikram Shah", b)])
    assert bank.match(unit(np.r_[0.9, 0.1, np.zeros(126)]), 0.4)[0] == "Anita Rao"
    assert bank.match(unit(np.r_[0.0, 0.0, 1.0, np.zeros(125)]), 0.4)[0] is None
    assert faces.from_bytes(faces.to_bytes(a)).tolist() == a.tolist()


class FakeEngine:
    """Every photo is "Anita"; a photo whose bytes say 'none' has no face."""

    def enroll(self, image):
        if image is None:
            raise faces.FaceError("no face found")
        return unit(np.r_[1.0, np.zeros(127)])


@pytest.fixture
def fake_engine(monkeypatch):
    monkeypatch.setattr(faces, "engine", lambda: FakeEngine())
    monkeypatch.setattr(faces, "decode", lambda data: None if data == b"none" else np.zeros((10, 10, 3), np.uint8))


def anita_id(db):
    from app.models import User
    return db.query(User).filter_by(name="Anita Rao").one().id


def test_enroll_needs_consent_then_keeps_only_fingerprints(roster, fake_engine):
    admin = login(client, "1111")
    uid = anita_id(roster)
    photos = [("photos", ("a.jpg", b"jpeg", "image/jpeg")), ("photos", ("b.jpg", b"none", "image/jpeg"))]
    assert client.post(f"/users/{uid}/face", files=photos, headers=admin).status_code == 422  # no consent
    out = client.post(f"/users/{uid}/face", files=photos, data={"consent": "true"}, headers=admin).json()
    assert out["added"] == 1 and out["samples"] == 1 and out["rejected"] == ["photo 2: no face found"]
    roster.expire_all()
    assert len(roster.query(FaceSample).filter_by(user_id=uid).one().embedding) == 128 * 4
    assert client.delete(f"/users/{uid}/face", headers=admin).json()["samples"] == 0  # their data, gone


def test_access_to_restricted_zones_is_set_per_person(roster):
    admin = login(client, "1111")
    uid = anita_id(roster)
    room = roster.query(Zone).filter_by(kind="restricted").first()
    other = roster.query(Zone).filter(Zone.kind != "restricted").first()
    out = client.put(f"/users/{uid}/access", json={"zone_ids": [room.id]}, headers=admin).json()
    assert out["zone_ids"] == [room.id]
    roster.expire_all()
    assert uid in roster.get(Zone, room.id).allowed_user_ids
    assert client.put(f"/users/{uid}/access", json={"zone_ids": [other.id]}, headers=admin).status_code == 422
    assert client.put(f"/users/{uid}/access", json={"zone_ids": []}, headers=admin).json()["zone_ids"] == []
    assert client.get(f"/users/{uid}/face", headers=login(client, "4444")).status_code == 403  # guards cannot


def test_the_watcher_names_people_in_restricted_zones(monkeypatch):
    from worker import run_worker
    from worker.detectors import Person

    class SeesAnita:
        def faces(self, image):
            return [faces.Face((0, 0, 50, 50), 0.9, unit(np.r_[1.0, np.zeros(127)]))]

    monkeypatch.setattr(run_worker, "face_engine", lambda: SeesAnita())
    watcher = run_worker.FaceWatcher()
    watcher.bank = faces.FaceBank([("Anita Rao", unit(np.r_[1.0, np.zeros(127)]))])
    inside = Person(track_id=7, xyxy=(100, 50, 200, 400), confidence=0.9, keypoints=[])
    outside = Person(track_id=8, xyxy=(500, 50, 600, 400), confidence=0.9, keypoints=[])
    half = ZoneDef("server-room", "restricted", [(0.0, 0.0), (0.5, 0.0), (0.5, 1.0), (0.0, 1.0)])
    names = watcher.update(np.zeros((480, 640, 3), np.uint8), [inside, outside], [half])
    assert names == {7: "Anita Rao"}  # only people inside a restricted zone are looked at


@pytest.mark.skipif(not os.environ.get("FACE_TEST_DIR"), reason="set FACE_TEST_DIR to test the real models")
def test_the_real_models_tell_people_apart():
    import cv2

    folder = Path(os.environ["FACE_TEST_DIR"])
    engine = faces.engine()
    bank = faces.FaceBank([("A", engine.enroll(cv2.imread(str(folder / "a1.jpg"))))])  # enrolled close up
    seen_a = engine.faces(cv2.imread(str(folder / "a2.jpg")))[0].embedding  # the camera sees them later
    seen_b = engine.faces(cv2.imread(str(folder / "b1.jpg")))[0].embedding  # someone else, a small face
    assert bank.match(seen_a)[0] == "A" and bank.match(seen_b)[0] is None
