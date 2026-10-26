"""POST /demo/incident: a test alarm from the dashboard that runs the real call ladder."""

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import app
from app.models import AuditLog, Incident, User
from app.services.escalation import escalation
from tests.conftest import login

client = TestClient(app)


def test_admin_raises_a_demo_fire(roster, monkeypatch):
    started = []
    monkeypatch.setattr(escalation, "start", started.append)
    anita = roster.scalar(select(User).where(User.name == "Anita Rao"))
    anita.actions = [*anita.actions, "run_demo"]
    roster.commit()
    out = client.post("/demo/incident", json={"type": "fire"}, headers=login(client, "1111"))
    assert out.status_code == 201, out.text
    body = out.json()
    assert (body["type"], body["severity"], body["status"]) == ("fire", "critical", "open")
    assert body["camera_id"] == "cam-phone"
    assert body["timeline"][0]["action"] == "detected"
    assert started == [body["id"]]
    audit = roster.scalar(select(AuditLog).where(AuditLog.action == "demo_incident"))
    assert audit.target == f"incident:{body['id']}"


def test_demo_severity_and_camera_choice(roster, monkeypatch):
    monkeypatch.setattr(escalation, "start", lambda _id: None)
    anita = roster.scalar(select(User).where(User.name == "Anita Rao"))
    anita.actions = [*anita.actions, "run_demo"]
    roster.commit()
    headers = login(client, "1111")
    access = client.post("/demo/incident", json={"type": "access", "camera_id": "cam-webcam"}, headers=headers).json()
    assert access["severity"] in ("high", "critical") and access["camera_id"] == "cam-webcam"
    assert client.post("/demo/incident", json={"type": "ppe"}, headers=headers).json()["severity"] == "low"
    assert client.post("/demo/incident", json={"type": "smoke"}, headers=headers).status_code == 422
    assert client.post("/demo/incident", json={"type": "fire", "camera_id": "nope"}, headers=headers).status_code == 404


def test_demo_needs_the_run_demo_action(roster):
    assert client.post("/demo/incident", json={"type": "fire"}, headers=login(client, "4444")).status_code == 403
    assert roster.scalar(select(Incident.id)) is None
