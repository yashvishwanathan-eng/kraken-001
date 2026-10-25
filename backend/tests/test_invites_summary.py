"""P19 invitations (email + QR, one-time link, PIN set on accept) and P20 sector summaries."""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app
from app.models import User
from app.routers.auth import reset_login_limits
from app.services import camera_status
from app.services.email_sender import emailer
from tests.conftest import DEMO_PINS, login, make_incident

client = TestClient(app)


def recent(path: str = "/summary") -> str:
    """The last two hours as a window, so the tests do not depend on the time of day (midnight IST)."""
    now = datetime.now(timezone.utc)
    start, end = now - timedelta(hours=2), now + timedelta(minutes=1)
    fmt = "%Y-%m-%dT%H:%M:%SZ"  # Z, not +00:00: a plus sign in a URL becomes a space
    return f"{path}?from={start.strftime(fmt)}&to={end.strftime(fmt)}"
INVITE = {"name": "Meera Iyer", "email": "meera@example.com", "role": "Guard",
          "camera_ids": ["cam-webcam"], "actions": ["ack"]}


@pytest.fixture
def mail(monkeypatch):
    monkeypatch.setattr(get_settings(), "email_mode", "simulate")
    monkeypatch.setattr(get_settings(), "dashboard_url", "http://192.168.137.1:5173")
    emailer.sent.clear()
    yield emailer
    emailer.sent.clear()


@pytest.fixture
def admin(roster, mail):
    reset_login_limits()
    camera_status.clear()
    return login(client, DEMO_PINS["anita"])


def _invite(headers, **overrides):
    return client.post("/invites", json={**INVITE, **overrides}, headers=headers)


def _token_from(created: dict) -> str:
    return created["link"].rsplit("/invite/", 1)[1]


# ---------- P19 ----------

def test_invite_creates_inactive_user_emails_link_and_returns_qr(roster, admin, mail):
    response = _invite(admin)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "pending" and body["email_status"] == "simulated"
    assert body["link"].startswith("http://192.168.137.1:5173/invite/")
    assert body["qr_png"].startswith("data:image/png;base64,")
    assert mail.sent == [("meera@example.com", "You are invited to RingGuard")]
    user = roster.get(User, body["user_id"])
    assert user.active is False  # cannot log in until the invitation is accepted


def test_invite_is_refused_for_unknown_camera_and_action_and_bad_email(admin):
    assert _invite(admin, camera_ids=["cam-nope"]).status_code == 422
    assert _invite(admin, actions=["fly"]).status_code == 422
    assert _invite(admin, email="not-an-email").status_code == 422


def test_only_manage_users_can_invite(roster, mail):
    # Vikram (Manager) and Suresh (Guard) do not hold manage_users in the demo roster
    assert _invite(login(client, DEMO_PINS["vikram"])).status_code == 403
    assert _invite(login(client, DEMO_PINS["suresh"])).status_code == 403


def test_link_previews_then_accept_sets_pin_and_logs_in_once(roster, admin):
    token = _token_from(_invite(admin).json())
    preview = client.get(f"/invites/link/{token}")
    assert preview.status_code == 200 and preview.json()["name"] == "Meera Iyer"

    accepted = client.post(f"/invites/link/{token}/accept", json={"pin": "5555"})
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["user"]["name"] == "Meera Iyer"
    # the link is one-time
    assert client.get(f"/invites/link/{token}").status_code == 404
    assert client.post(f"/invites/link/{token}/accept", json={"pin": "6666"}).status_code == 404
    # and the new PIN now logs in
    assert client.post("/auth/pin", json={"pin": "5555"}).status_code == 200


def test_accept_refuses_a_pin_already_in_use(roster, admin):
    token = _token_from(_invite(admin).json())
    response = client.post(f"/invites/link/{token}/accept", json={"pin": DEMO_PINS["ravi"]})
    assert response.status_code == 409
    assert client.get(f"/invites/link/{token}").status_code == 200  # still valid, nothing consumed


def test_expired_link_is_not_valid(roster, admin):
    token = _token_from(_invite(admin).json())
    from app.db import SessionLocal
    from app.models import Invitation
    from app.routers.invites import _hash
    with SessionLocal() as session:
        inv = session.query(Invitation).filter(Invitation.token_hash == _hash(token)).one()
        inv.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        session.commit()
    assert client.get(f"/invites/link/{token}").status_code == 404


def test_revoked_invitation_cannot_be_accepted(roster, admin):
    created = _invite(admin).json()
    assert client.post(f"/invites/{created['invite_id']}/revoke", headers=admin).status_code == 200
    assert client.post(f"/invites/link/{_token_from(created)}/accept", json={"pin": "5555"}).status_code == 404


def test_one_pending_invite_per_email(roster, admin):
    assert _invite(admin).status_code == 201
    assert _invite(admin).status_code == 409


def test_email_off_still_returns_the_link(roster, admin, monkeypatch):
    monkeypatch.setattr(get_settings(), "email_mode", "off")
    body = _invite(admin, email="other@example.com").json()
    assert body["email_status"] == "off" and body["link"]


def test_failed_email_is_reported_not_hidden(roster, admin, monkeypatch):
    monkeypatch.setattr(get_settings(), "email_mode", "live")
    monkeypatch.setattr(get_settings(), "smtp_host", "127.0.0.1")
    monkeypatch.setattr(get_settings(), "smtp_port", 1)  # nothing listens here
    monkeypatch.setattr(get_settings(), "smtp_from", "RingGuard <a@example.com>")
    monkeypatch.setattr("app.services.email_sender.TIMEOUT_S", 0.5)
    body = _invite(admin, email="fail@example.com").json()
    assert body["email_status"] == "not_sent" and body["link"]


def test_invitation_list_is_visible_to_admins_only(roster, admin):
    _invite(admin)
    assert len(client.get("/invites", headers=admin).json()) == 1
    assert client.get("/invites", headers=login(client, "4444")).status_code == 403


# ---------- P20 ----------

def test_summary_says_no_violations_when_there_are_no_entries(roster, admin):
    response = client.get(recent(), headers=admin)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["violations"]["restricted_entries"] == 0
    assert "No violations" in body["text"]
    assert body["sector"]["all_cameras"] is True


def test_summary_counts_restricted_entries_and_open_ones(roster, admin, db):
    make_incident(db, camera_id="cam-webcam", type_="access", status="open")
    make_incident(db, camera_id="cam-webcam", type_="access", status="resolved")
    response = client.get(recent(), headers=admin).json()
    assert response["violations"]["restricted_entries"] == 2
    assert response["violations"]["still_open"] == 1
    assert "2 restricted-area entries" in response["text"]


def test_head_guard_sees_only_their_cameras(roster, db):
    reset_login_limits()
    camera_status.clear()
    head = login(client, DEMO_PINS["ravi"])  # Ravi: cam-webcam + cam-phone in the old fixtures
    make_incident(db, camera_id="cam-webcam", type_="fall", severity="critical")
    make_incident(db, camera_id="cam-phone", type_="fall", severity="critical")
    body = client.get(recent(), headers=head).json()
    assert body["sector"]["all_cameras"] is False
    assert set(body["sector"]["camera_ids"]) == {"cam-phone", "cam-webcam"}
    assert body["incidents"]["total"] == 2


def test_guard_summary_has_no_login_logs(roster, admin):
    client.post("/auth/pin", json={"pin": DEMO_PINS["suresh"]})  # creates a login row
    guard = login(client, DEMO_PINS["suresh"])
    assert client.get(recent(), headers=guard).json()["logins"] == []
    assert len(client.get(recent(), headers=admin).json()["logins"]) >= 1


def test_summary_rejects_a_backwards_window(roster, admin):
    response = client.get("/summary?from=2026-10-25T10:00:00Z&to=2026-10-25T09:00:00Z", headers=admin)
    assert response.status_code == 422


def test_summary_needs_a_login(roster):
    assert client.get("/summary").status_code == 401
