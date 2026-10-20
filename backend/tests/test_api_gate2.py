"""Gate 2: login, roles, camera delegation and the real API, against the test database."""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.config import get_settings
from app.main import app
from app.models import AuditLog, Incident, TimelineEvent
from app.routers.auth import reset_login_limits
from app.seed import seed_cameras, seed_users, seed_zones
from app.services import camera_status

PINS = {"anita": "1111", "vikram": "2222", "ravi": "3333", "suresh": "4444"}
client = TestClient(app)


@pytest.fixture
def seeded(db):
    reset_login_limits()
    camera_status.clear()
    seed_cameras(db)
    db.flush()
    seed_zones(db)
    seed_users(db, pins=PINS)
    db.commit()
    return db


def login(pin):
    response = client.post("/auth/pin", json={"pin": pin})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['token']}"}


@pytest.fixture
def tokens(seeded):
    return {key: login(pin) for key, pin in PINS.items()}


def add_incident(db, camera_id, type_="access", severity="high", status="open", minutes_ago=10):
    started = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    incident = Incident(type=type_, severity=severity, status=status, camera_id=camera_id, zone="z",
                        started_at=started, confidence=0.9)
    db.add(incident)
    db.flush()
    db.add(TimelineEvent(incident_id=incident.id, at=started, action="detected"))
    db.commit()
    return incident.id


# ---------- login ----------

def test_login_returns_token_and_user(seeded):
    response = client.post("/auth/pin", json={"pin": "4444"})
    assert response.status_code == 200
    body = response.json()
    assert body["user"] == {"id": 4, "name": "Suresh Babu", "role": "Guard"} and len(body["token"]) > 30


def test_wrong_pin_is_401_and_bad_format_is_422(seeded):
    assert client.post("/auth/pin", json={"pin": "9999"}).status_code == 401
    assert client.post("/auth/pin", json={"pin": "12"}).status_code == 422
    assert client.post("/auth/pin", json={"pin": "abcd"}).status_code == 422
    assert client.post("/auth/pin", json={}).status_code == 422


def test_too_many_wrong_pins_is_429(seeded):
    for _ in range(get_settings().login_max_failures):
        assert client.post("/auth/pin", json={"pin": "9999"}).status_code == 401
    assert client.post("/auth/pin", json={"pin": "1111"}).status_code == 429


def test_no_token_is_401(seeded):
    for path in ("/me", "/cameras", "/events", "/stats", "/zones", "/responders"):
        assert client.get(path).status_code == 401, path
    assert client.get("/me", headers={"Authorization": "Bearer made-up"}).status_code == 401


def test_me_shows_delegated_cameras(tokens):
    assert client.get("/me", headers=tokens["suresh"]).json()["camera_ids"] == ["cam-webcam"]
    admin = client.get("/me", headers=tokens["anita"]).json()
    assert admin["camera_ids"] == ["cam-phone", "cam-webcam"] and "manage_users" in admin["actions"]


# ---------- users ----------

def test_only_user_managers_reach_users(tokens):
    assert client.get("/users", headers=tokens["suresh"]).status_code == 403
    users = client.get("/users", headers=tokens["anita"]).json()
    assert len(users) == 4 and all("pin" not in u and "pin_hash" not in u for u in users)


def test_create_user_validates(tokens):
    base = {"name": "New Guard", "role": "Guard", "camera_ids": ["cam-webcam"], "actions": ["ack"], "pin": "5555"}
    admin = tokens["anita"]
    assert client.post("/users", json={**base, "pin": "4444"}, headers=admin).status_code == 409  # PIN taken
    assert client.post("/users", json={**base, "actions": ["fly"]}, headers=admin).status_code == 422
    assert client.post("/users", json={**base, "camera_ids": ["cam-x"]}, headers=admin).status_code == 422
    created = client.post("/users", json=base, headers=admin)
    assert created.status_code == 201 and "pin" not in created.json()
    assert client.post("/auth/pin", json={"pin": "5555"}).status_code == 200


def test_pin_reset_logs_the_user_out(tokens):
    assert client.put("/users/4", json={"pin": "7777"}, headers=tokens["anita"]).status_code == 200
    assert client.get("/me", headers=tokens["suresh"]).status_code == 401  # old token is dead
    assert client.post("/auth/pin", json={"pin": "7777"}).status_code == 200


def test_admin_cannot_lock_themselves_out(tokens):
    assert client.put("/users/1", json={"active": False}, headers=tokens["anita"]).status_code == 400
    assert client.delete("/users/1", headers=tokens["anita"]).status_code == 400


# ---------- cameras and streams ----------

def test_guard_sees_only_their_camera_with_signed_url(tokens):
    cameras = client.get("/cameras", headers=tokens["suresh"]).json()
    assert [c["id"] for c in cameras] == ["cam-webcam"]
    assert cameras[0]["stream_url"].startswith("/stream/cam-webcam?st=4.")
    assert {z["name"] for z in cameras[0]["zones"]} == {"server-room-door", "assembly-line"}


def test_stream_url_refresh_respects_delegation(tokens):
    assert client.get("/cameras/cam-phone/stream-url", headers=tokens["suresh"]).status_code == 403
    assert client.get("/cameras/cam-nope/stream-url", headers=tokens["anita"]).status_code == 404
    assert client.get("/cameras/cam-phone/stream-url", headers=tokens["anita"]).json()["stream_url"].startswith(
        "/stream/cam-phone?st=1.")


def test_stream_token_checks(tokens):
    url = client.get("/cameras", headers=tokens["suresh"]).json()[0]["stream_url"]
    st = url.split("st=")[1]
    assert client.get("/stream/cam-webcam?st=bad").status_code == 401
    assert client.get(f"/stream/cam-phone?st={st}").status_code == 401  # signed for another camera
    assert client.get(url).status_code == 503  # genuine, but no worker is running in tests


# ---------- events ----------

def test_events_are_filtered_by_camera(seeded, tokens):
    webcam = add_incident(seeded, "cam-webcam")
    phone = add_incident(seeded, "cam-phone", type_="fall", severity="critical")
    assert [e["id"] for e in client.get("/events", headers=tokens["suresh"]).json()] == [webcam]
    assert {e["id"] for e in client.get("/events", headers=tokens["anita"]).json()} == {webcam, phone}
    assert client.get(f"/events/{phone}", headers=tokens["suresh"]).status_code == 404  # hidden, not 403
    filtered = client.get("/events", params={"type": "fall"}, headers=tokens["anita"]).json()
    assert [e["id"] for e in filtered] == [phone]
    assert client.get("/events", params={"type": "flood"}, headers=tokens["anita"]).status_code == 422


def test_ack_is_idempotent_and_audited(seeded, tokens):
    incident_id = add_incident(seeded, "cam-webcam")
    first = client.post(f"/events/{incident_id}/ack", headers=tokens["suresh"])
    assert first.status_code == 200
    body = first.json()
    assert body["status"] == "acknowledged" and body["acknowledged_by"] == "Suresh Babu"
    assert body["timeline"][-1] | {"at": None} == {"at": None, "action": "acknowledged", "by": "Suresh Babu",
                                                     "via": "dashboard"}
    again = client.post(f"/events/{incident_id}/ack", headers=tokens["suresh"]).json()
    assert len(again["timeline"]) == 2  # detected + one acknowledged, not two
    seeded.expire_all()
    assert seeded.query(AuditLog).filter_by(action="ack").count() == 1


def test_resolve_needs_permission_and_closes_once(seeded, tokens):
    incident_id = add_incident(seeded, "cam-webcam")
    body = {"outcome": "false_alarm", "note": "cleaner with a trolley"}
    assert client.post(f"/events/{incident_id}/resolve", json=body, headers=tokens["suresh"]).status_code == 403
    assert client.post(f"/events/{incident_id}/resolve", json={"outcome": "maybe"},
                       headers=tokens["ravi"]).status_code == 422
    closed = client.post(f"/events/{incident_id}/resolve", json=body, headers=tokens["ravi"])
    assert closed.status_code == 200 and closed.json()["status"] == "false_alarm"
    assert client.post(f"/events/{incident_id}/resolve", json=body, headers=tokens["ravi"]).status_code == 409
    assert client.post(f"/events/{incident_id}/ack", headers=tokens["ravi"]).status_code == 409


def test_stats(seeded, tokens):
    a = add_incident(seeded, "cam-webcam", minutes_ago=30)
    add_incident(seeded, "cam-webcam", type_="loitering", severity="low", status="resolved", minutes_ago=20)
    add_incident(seeded, "cam-phone", type_="fall", severity="critical", minutes_ago=10)
    client.post(f"/events/{a}/ack", headers=tokens["anita"])
    stats = client.get("/stats", headers=tokens["anita"]).json()
    assert stats["by_type"] == {"access": 1, "loitering": 1, "fall": 1}
    assert stats["open"] == 2 and stats["resolved"] == 1
    assert 30 * 60 - 5 <= stats["avg_response_s"] <= 30 * 60 + 60
    assert sum(d["count"] for d in stats["by_day"]) == 3
    guard = client.get("/stats", headers=tokens["suresh"]).json()
    assert "fall" not in guard["by_type"]  # phone camera is not his


# ---------- audit, zones, responders ----------

def test_audit_log_is_for_admin_and_manager(tokens):
    assert client.get("/audit-log", headers=tokens["suresh"]).status_code == 403
    rows = client.get("/audit-log", params={"action": "login"}, headers=tokens["vikram"]).json()
    assert {r["user"] for r in rows} == {"Anita Rao", "Vikram Shah", "Ravi Kumar", "Suresh Babu"}
    assert all(r["channel"] == "dashboard" for r in rows)


def test_zone_create_validates(tokens):
    zone = {"camera_id": "cam-webcam", "name": "battery-store", "kind": "restricted",
            "polygon": [[0.1, 0.1], [0.3, 0.1], [0.3, 0.3]]}
    assert client.post("/zones", json=zone, headers=tokens["suresh"]).status_code == 403
    assert client.post("/zones", json={**zone, "polygon": [[0.1, 0.1], [1.5, 0.1], [0.3, 0.3]]},
                       headers=tokens["vikram"]).status_code == 422
    assert client.post("/zones", json={**zone, "polygon": [[0.1, 0.1], [0.2, 0.2]]},
                       headers=tokens["vikram"]).status_code == 422
    created = client.post("/zones", json=zone, headers=tokens["vikram"])
    assert created.status_code == 201 and created.json()["kind"] == "restricted"
    guard_zones = client.get("/zones", headers=tokens["suresh"]).json()
    assert {z["camera_id"] for z in guard_zones} == {"cam-webcam"}


def test_responders_validate_phone_and_time(tokens):
    good = {"name": "Ravi Kumar", "phone": "+919876543210", "language": "ta", "zone": "loading-bay",
            "shift_start": "08:00", "shift_end": "20:00", "call_order": 1, "active": True}
    assert client.post("/responders", json={**good, "phone": "98765"}, headers=tokens["anita"]).status_code == 422
    assert client.post("/responders", json={**good, "shift_end": "25:00"}, headers=tokens["anita"]).status_code == 422
    assert client.post("/responders", json=good, headers=tokens["suresh"]).status_code == 403
    created = client.post("/responders", json=good, headers=tokens["anita"])
    assert created.status_code == 201 and created.json()["shift_start"] == "08:00"
    assert len(client.get("/responders", headers=tokens["suresh"]).json()) == 1


# ---------- heartbeat and websocket ----------

def test_heartbeat_marks_camera_online(seeded):
    key = {"X-Internal-Key": get_settings().internal_key}
    beat = {"camera_id": "cam-webcam", "fps": 9.4, "online": True, "ts": "2026-10-20T10:00:00Z"}
    assert client.post("/internal/heartbeat", json=beat, headers={"X-Internal-Key": "x"}).status_code == 401
    assert client.post("/internal/heartbeat", json=beat, headers=key).status_code == 200
    cameras = {c["id"]: c for c in client.get("/health").json()["cameras"]}
    assert cameras["cam-webcam"] == {"id": "cam-webcam", "online": True, "fps": 9.4}
    assert cameras["cam-phone"]["online"] is False


def test_websocket_needs_a_token(seeded):
    with pytest.raises(WebSocketDisconnect) as closed:
        with client.websocket_connect("/ws/events?token=bad") as ws:
            ws.receive_json()
    assert closed.value.code == 4401


def test_websocket_only_sends_delegated_cameras(seeded):
    token = client.post("/auth/pin", json={"pin": "4444"}).json()["token"]
    phone = add_incident(seeded, "cam-phone")
    webcam = add_incident(seeded, "cam-webcam")
    key = {"X-Internal-Key": get_settings().internal_key}
    with TestClient(app) as live, live.websocket_connect(f"/ws/events?token={token}") as ws:
        live.post(f"/internal/incidents/{phone}/created", headers=key)  # not his camera: must not arrive
        live.post(f"/internal/incidents/{webcam}/created", headers=key)
        assert ws.receive_json()["id"] == webcam
