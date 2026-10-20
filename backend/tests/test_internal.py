from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app
from app.models import Camera, Incident, TimelineEvent
from app.ws import manager

client = TestClient(app)


class FakeSocket:
    """Stands in for a dashboard's WebSocket and records what it was sent."""

    def __init__(self):
        self.sent = []

    async def send_json(self, message):
        self.sent.append(message)


def make_incident(db):
    db.add(Camera(id="cam-east-wing", name="East Wing, Loading Bay 2", source="0", port=8101))
    started = datetime(2026, 10, 24, 10, 12, 41, 123456, tzinfo=timezone.utc)
    incident = Incident(type="access", severity="high", status="open", camera_id="cam-east-wing",
                        zone="loading-bay", started_at=started, confidence=0.874,
                        snapshot_path="1.jpg", clip_path="1.mp4")
    db.add(incident)
    db.flush()
    db.add(TimelineEvent(incident_id=incident.id, at=started, action="detected"))
    db.commit()
    return incident.id


def test_wrong_internal_key_is_rejected(db):
    incident_id = make_incident(db)
    response = client.post(f"/internal/incidents/{incident_id}/created", headers={"X-Internal-Key": "nope"})
    assert response.status_code == 401


def test_unknown_incident_is_404(db):
    response = client.post("/internal/incidents/999/created",
                           headers={"X-Internal-Key": get_settings().internal_key})
    assert response.status_code == 404


def test_created_incident_is_broadcast_in_contract_shape(db):
    incident_id = make_incident(db)
    socket = FakeSocket()
    manager._clients[socket] = lambda message: True
    try:
        response = client.post(f"/internal/incidents/{incident_id}/created",
                               headers={"X-Internal-Key": get_settings().internal_key})
    finally:
        manager.disconnect(socket)
    assert response.status_code == 200 and response.json() == {"ok": True}
    assert socket.sent == [{
        "id": incident_id, "type": "access", "severity": "high", "status": "open",
        "camera_id": "cam-east-wing", "camera_name": "East Wing, Loading Bay 2", "zone": "loading-bay",
        "started_at": "2026-10-24T10:12:41Z", "confidence": 0.87,
        "snapshot_url": "/media/1.jpg", "clip_url": "/media/1.mp4", "acknowledged_by": None,
        "timeline": [{"at": "2026-10-24T10:12:41Z", "action": "detected"}],
    }]


def test_filtered_out_client_gets_nothing(db):
    incident_id = make_incident(db)
    socket = FakeSocket()
    manager._clients[socket] = lambda message: message["camera_id"] == "cam-other"
    try:
        client.post(f"/internal/incidents/{incident_id}/created",
                    headers={"X-Internal-Key": get_settings().internal_key})
    finally:
        manager.disconnect(socket)
    assert socket.sent == []
