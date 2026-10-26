"""Every person is added with a phone number and lands on the call list (alert all guards reaches them)."""

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import app
from app.models import Responder, User
from app.services.directory import on_shift_contacts
from tests.conftest import login

client = TestClient(app)
NEW = {"name": "Kavya Rao", "role": "Guard", "pin": "5656", "camera_ids": ["cam-webcam"], "actions": ["ack"],
       "phone": "+919811111111", "zone": "entrance"}


def test_adding_a_person_needs_a_valid_phone(roster):
    admin = login(client, "1111")
    missing = {k: v for k, v in NEW.items() if k != "phone"}
    assert client.post("/users", json=missing, headers=admin).status_code == 422
    assert client.post("/users", json={**NEW, "phone": "98111"}, headers=admin).status_code == 422


def test_new_person_is_on_the_call_list_and_reached_by_alert_all(roster):
    admin = login(client, "1111")
    out = client.post("/users", json=NEW, headers=admin)
    assert out.status_code == 201, out.text
    body = out.json()
    assert (body["phone"], body["zone"], body["shift_start"], body["shift_end"]) == (
        "+919811111111", "entrance", "00:00", "00:00")
    row = roster.scalar(select(Responder).where(Responder.user_id == body["id"]))
    assert (row.name, row.phone, row.active) == ("Kavya Rao", "+919811111111", True)
    assert "Kavya Rao" in [c.name for c in on_shift_contacts(roster)]  # nobody on duty yet: shift roster
    listed = next(u for u in client.get("/users", headers=admin).json() if u["id"] == body["id"])
    assert listed["phone"] == "+919811111111"


def test_editing_phone_and_deactivating_update_the_call_list(roster):
    admin = login(client, "1111")
    user_id = client.post("/users", json=NEW, headers=admin).json()["id"]
    client.put(f"/users/{user_id}", json={"phone": "+919822222222", "zone": "gate-2"}, headers=admin)
    roster.expire_all()
    row = roster.scalar(select(Responder).where(Responder.user_id == user_id))
    assert (row.phone, row.zone) == ("+919822222222", "gate-2")
    client.put(f"/users/{user_id}", json={"active": False}, headers=admin)
    roster.expire_all()
    assert roster.get(Responder, row.id).active is False
    assert "Kavya Rao" not in [c.name for c in on_shift_contacts(roster)]
    assert client.delete(f"/users/{user_id}", headers=admin).status_code == 204
    row_id = row.id
    roster.expunge_all()
    assert roster.scalar(select(Responder).where(Responder.id == row_id)) is None  # number never stays on the list


def test_invited_person_is_called_only_after_accepting(roster, monkeypatch):
    from app.services.email_sender import emailer
    monkeypatch.setattr(emailer, "send", lambda *a: "simulated")
    out = client.post("/invites", json={"name": "Arjun", "email": "arjun@example.com", "role": "Guard",
                                        "phone": "+919833333333"}, headers=login(client, "1111")).json()
    row = roster.scalar(select(Responder).where(Responder.user_id == out["user_id"]))
    assert row.phone == "+919833333333"
    assert "Arjun" not in [c.name for c in on_shift_contacts(roster)]  # account not active yet
    roster.get(User, out["user_id"]).active = True
    roster.commit()
    assert "Arjun" in [c.name for c in on_shift_contacts(roster)]
