"""Sites and areas, guard profiles with encrypted personal details, badge scans, presence, delegation."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.config import get_settings
from app.main import app
from app.models import AuditLog, BadgeScan, Camera, GuardProfile, User
from app.routers.auth import reset_login_limits
from app.services import camera_status
from app.services.badges import entry_badge_status
from app.services.escalation import alert_text
from tests.conftest import DEMO_PINS, login, make_incident

client = TestClient(app)
DETAILS = {"badge_number": "RG-7781", "badge_id": "BID-9920", "security_number": "SEC-55-204",
           "emergency_contact": "Meena +919800000099"}


@pytest.fixture
def key(roster, monkeypatch):
    monkeypatch.setattr(get_settings(), "data_key", Fernet.generate_key().decode())
    reset_login_limits()
    camera_status.clear()
    return get_settings().data_key


@pytest.fixture
def admin(key):
    return login(client, DEMO_PINS["anita"])


def _user_id(db, name: str) -> int:
    return db.scalar(select(User.id).where(User.name == name))


def _build_site(headers) -> dict:
    site = client.post("/sites", json={"name": "Plant 1", "address": "Hosur Road"}, headers=headers).json()
    building = client.post(f"/sites/{site['id']}/buildings", json={"name": "Block A"}, headers=headers).json()
    room = client.post(f"/buildings/{building['id']}/areas", json={"name": "Server room", "kind": "room"},
                       headers=headers).json()
    return {"site": site, "building": building, "room": room}


# ---------- sites, buildings, areas ----------

def test_site_tree_and_camera_placement(roster, admin):
    tree = _build_site(admin)
    assert client.put("/cameras/cam-webcam/area", json={"area_id": tree["room"]["id"]}, headers=admin).status_code == 200
    sites = client.get("/sites", headers=admin).json()["sites"]
    area = sites[0]["buildings"][0]["areas"][0]
    assert area["name"] == "Server room" and area["camera_ids"] == ["cam-webcam"]


def test_only_manage_sites_can_build_the_tree(roster):
    guard = login(client, DEMO_PINS["suresh"])
    assert client.post("/sites", json={"name": "X"}, headers=guard).status_code == 403


def test_duplicate_site_and_unknown_area_are_refused(roster, admin):
    _build_site(admin)
    assert client.post("/sites", json={"name": "Plant 1"}, headers=admin).status_code == 409
    assert client.put("/cameras/cam-webcam/area", json={"area_id": 999}, headers=admin).status_code == 422


# ---------- guard details ----------

def test_admin_sees_personal_details_and_they_are_encrypted_in_the_database(roster, admin, key):
    uid = _user_id(roster, "Suresh Babu")
    response = client.put(f"/guards/{uid}/profile", json={"position": "Shift lead", "post": "Gate 2",
                                                          "has_phone": True, "personal": DETAILS}, headers=admin)
    assert response.status_code == 200, response.text
    assert response.json()["has_personal_details"] is True and response.json()["personal"] is None  # never echoed back
    detail = client.get(f"/guards/{uid}/profile", headers=admin).json()
    assert detail["personal"] == DETAILS
    raw = roster.get(GuardProfile, uid).personal
    assert b"RG-7781" not in raw and b"Meena" not in raw  # never plain text in the database


def test_manager_sees_names_but_never_personal_details(roster, admin):
    uid = _user_id(roster, "Suresh Babu")
    client.put(f"/guards/{uid}/profile", json={"post": "Gate 2", "personal": DETAILS}, headers=admin)
    manager = login(client, DEMO_PINS["vikram"])
    names = [g["name"] for g in client.get("/guards", headers=manager).json()]
    assert "Suresh Babu" in names
    assert client.get(f"/guards/{uid}/profile", headers=manager).status_code == 403
    assert "RG-7781" not in client.get("/guards", headers=manager).text


def test_saving_replaces_the_previous_details(roster, admin):
    uid = _user_id(roster, "Suresh Babu")
    client.put(f"/guards/{uid}/profile", json={"personal": DETAILS}, headers=admin)
    client.put(f"/guards/{uid}/profile", json={"personal": {"badge_number": "RG-0001"}}, headers=admin)
    detail = client.get(f"/guards/{uid}/profile", headers=admin).json()["personal"]
    assert detail == {"badge_number": "RG-0001", "badge_id": "", "security_number": "", "emergency_contact": ""}


def test_audit_lists_changed_fields_not_values(roster, admin):
    uid = _user_id(roster, "Suresh Babu")
    client.put(f"/guards/{uid}/profile", json={"post": "Gate 2", "personal": DETAILS}, headers=admin)
    row = roster.scalar(select(AuditLog).where(AuditLog.action == "guard_profile_update").order_by(AuditLog.id.desc()))
    assert "personal" in row.detail["changed"] and "post" in row.detail["changed"]
    assert "RG-7781" not in str(row.detail)


def test_saving_personal_details_needs_a_data_key(roster, monkeypatch):
    monkeypatch.setattr(get_settings(), "data_key", "")
    reset_login_limits()
    headers = login(client, DEMO_PINS["anita"])
    uid = _user_id(roster, "Suresh Babu")
    assert client.put(f"/guards/{uid}/profile", json={"personal": DETAILS}, headers=headers).status_code == 503


def test_duplicate_badge_number_is_refused(roster, admin):
    a, b = _user_id(roster, "Suresh Babu"), _user_id(roster, "Ravi Kumar")
    assert client.put(f"/guards/{a}/profile", json={"personal": DETAILS}, headers=admin).status_code == 200
    assert client.put(f"/guards/{b}/profile", json={"personal": DETAILS}, headers=admin).status_code == 409


# ---------- badge scans and presence ----------

def _badge_setup(db, admin):
    uid = _user_id(db, "Suresh Babu")
    client.put(f"/guards/{uid}/profile", json={"personal": DETAILS}, headers=admin)
    return uid


def test_scan_records_the_guard_and_refuses_unknown_badges(roster, admin):
    _badge_setup(roster, admin)
    tree = _build_site(admin)
    head = login(client, DEMO_PINS["ravi"])  # Head Guard: scan_badges
    ok = client.post("/badges/scan", json={"badge_number": "RG-7781", "area_id": tree["room"]["id"],
                                           "reader": "door-1"}, headers=head)
    assert ok.status_code == 201 and ok.json()["guard_name"] == "Suresh Babu"
    assert "RG-7781" not in ok.text
    assert client.post("/badges/scan", json={"badge_number": "RG-0000"}, headers=head).status_code == 404
    assert client.post("/badges/scan", json={"badge_number": "RG-7781"},
                       headers=login(client, DEMO_PINS["vikram"])).status_code == 403  # no scan_badges


def test_presence_shows_names_and_areas_within_the_callers_scope(roster, admin):
    _badge_setup(roster, admin)
    tree = _build_site(admin)
    client.put("/cameras/cam-webcam/area", json={"area_id": tree["room"]["id"]}, headers=admin)
    client.post("/badges/scan", json={"badge_number": "RG-7781", "area_id": tree["room"]["id"]},
                headers=login(client, DEMO_PINS["ravi"]))
    now = client.get("/presence", headers=admin).json()
    assert [p["name"] for p in now["present"]] == ["Suresh Babu"]
    assert now["present"][0]["area_name"] == "Server room"
    assert "RG-7781" not in str(now)
    # a camera outside the caller's scope hides the area; the guard (only cam-webcam) still sees it here
    assert client.get("/presence", headers=login(client, DEMO_PINS["suresh"])).json()["present"] != []


def test_presence_forgets_old_scans(roster, admin):
    _badge_setup(roster, admin)
    tree = _build_site(admin)
    roster.add(BadgeScan(at=datetime.now(timezone.utc) - timedelta(hours=3), user_id=_user_id(roster, "Suresh Babu"),
                         area_id=tree["room"]["id"]))
    roster.commit()
    assert client.get("/presence", headers=admin).json()["present"] == []


def test_entry_is_scanned_only_when_a_badge_was_read_in_that_area_near_the_time(roster, admin):
    _badge_setup(roster, admin)
    tree = _build_site(admin)
    client.put("/cameras/cam-webcam/area", json={"area_id": tree["room"]["id"]}, headers=admin)
    entry_at = datetime.now(timezone.utc)
    assert entry_badge_status(roster, "cam-webcam", entry_at) == "not_scanned"
    client.post("/badges/scan", json={"badge_number": "RG-7781", "area_id": tree["room"]["id"]},
                headers=login(client, DEMO_PINS["ravi"]))
    assert entry_badge_status(roster, "cam-webcam", entry_at) == "scanned"
    assert entry_badge_status(roster, "cam-phone", entry_at) == "unknown"  # not placed in an area


def test_alert_text_says_when_there_was_no_badge():
    incident = SimpleNamespace(id=7, severity="high", type="access", zone="server-room-door", started_at=datetime.now(timezone.utc),
                               camera=SimpleNamespace(name="Restricted Door"))
    assert "No badge scanned" in alert_text(incident, "Asia/Kolkata", "not_scanned")
    assert "No badge" not in alert_text(incident, "Asia/Kolkata", "scanned")


def test_summary_counts_entries_without_a_badge(roster, admin):
    _badge_setup(roster, admin)
    tree = _build_site(admin)
    client.put("/cameras/cam-webcam/area", json={"area_id": tree["room"]["id"]}, headers=admin)
    make_incident(roster, camera_id="cam-webcam", type_="access", status="open")
    now = datetime.now(timezone.utc)
    window = f"?from={(now - timedelta(hours=2)).strftime('%Y-%m-%dT%H:%M:%SZ')}&to={(now + timedelta(minutes=1)).strftime('%Y-%m-%dT%H:%M:%SZ')}"
    body = client.get(f"/summary{window}", headers=admin).json()
    assert body["violations"]["without_badge"] == 1
    assert "no badge scan" in body["text"]


# ---------- delegation ----------

def test_manager_can_hand_down_only_cameras_and_actions_they_hold(roster):
    manager = login(client, DEMO_PINS["vikram"])  # cam-webcam + cam-phone, delegate_cameras
    base = {"name": "Sub Guard", "role": "Guard", "pin": "7777", "phone": "+919800000077"}
    assert client.post("/users", json={**base, "camera_ids": ["cam-webcam"], "actions": ["ack"]},
                       headers=manager).status_code == 201
    base["pin"] = "7778"
    assert client.post("/users", json={**base, "camera_ids": ["cam-webcam"], "actions": ["all_cameras"]},
                       headers=manager).status_code == 403  # actions he does not hold
    base["pin"] = "7779"
    roster.add(Camera(id="cam-extra", name="Extra", source="0", port=8199))
    roster.commit()
    assert client.post("/users", json={**base, "camera_ids": ["cam-extra"], "actions": ["ack"]},
                       headers=manager).status_code == 403  # a camera he cannot see


def test_guard_cannot_delegate(roster):
    guard = login(client, DEMO_PINS["suresh"])
    assert client.post("/users", json={"name": "X", "role": "Guard", "pin": "8888", "camera_ids": [],
                                       "actions": [], "phone": "+919800000088"}, headers=guard).status_code == 403
