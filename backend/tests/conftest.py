"""Test setup: point the app at a separate test database before anything imports app.db.

The test database is the dev DATABASE_URL with "_test" added to the database name
(override with TEST_DATABASE_URL). It is migrated once per run, and wiped between tests that use it.
If it cannot be reached, database tests are skipped instead of failing.
"""

import os

import pytest

from app.config import BACKEND_DIR, Settings


def _test_database_url() -> str:
    explicit = os.environ.get("TEST_DATABASE_URL")
    if explicit:
        return explicit
    dev_url = Settings().database_url
    base, _, name = dev_url.rpartition("/")
    return f"{base}/{name}_test" if dev_url else ""


os.environ["DATABASE_URL"] = _test_database_url()

# Tests use a frozen copy of the demo config, so editing config/*.yaml for the demo never breaks them.
FIXTURES = BACKEND_DIR / "tests" / "fixtures"
os.environ["CAMERA_CONFIG_PATH"] = str(FIXTURES / "cameras.yaml")


@pytest.fixture(scope="session")
def migrated_db():
    from alembic import command
    from alembic.config import Config

    if not os.environ["DATABASE_URL"]:
        pytest.skip("no DATABASE_URL configured")
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    try:
        command.upgrade(config, "head")
    except Exception as exc:  # database down or test database missing
        pytest.skip(f"test database not available: {exc}")
    return os.environ["DATABASE_URL"]


@pytest.fixture
def db(migrated_db):
    """A session on a freshly emptied test database."""
    from sqlalchemy import text

    from app.db import SessionLocal, engine
    from app.models import Base

    tables = ", ".join(t.name for t in Base.metadata.sorted_tables)
    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    with SessionLocal() as session:
        yield session


DEMO_PINS = {"anita": "1111", "vikram": "2222", "ravi": "3333", "suresh": "4444"}
DEMO_PHONES = {"anita": "+919800000001", "vikram": "+919800000002", "ravi": "+919800000003",
               "suresh": "+919800000004"}


@pytest.fixture
def roster(db):
    """Cameras, zones, the 4 demo users and a phone roster (fake numbers) in the test database."""
    from app.routers.auth import reset_login_limits
    from app.seed import seed_cameras, seed_responders, seed_users, seed_zones
    from app.services import camera_status

    reset_login_limits()
    camera_status.clear()
    seed_cameras(db)
    db.flush()
    seed_zones(db, FIXTURES / "zones.yaml")
    seed_users(db, FIXTURES / "demo_users.yaml", pins=DEMO_PINS)
    db.flush()
    seed_responders(db, FIXTURES / "demo_responders.yaml", phones=DEMO_PHONES)
    db.commit()
    return db


@pytest.fixture
def simulate(monkeypatch):
    """Calls and WhatsApp in simulate mode: rows and timeline entries are written, nobody is dialled."""
    from app.config import get_settings
    from app.services.whatsapp import whatsapp

    monkeypatch.setattr(get_settings(), "calls_mode", "simulate")
    monkeypatch.setattr(get_settings(), "whatsapp_mode", "simulate")
    whatsapp.sent.clear()
    yield whatsapp
    whatsapp.sent.clear()


TWILIO_TOKEN = "test-twilio-auth-token"
PUBLIC_URL = "https://safety.example.test"


@pytest.fixture
def twilio_on(monkeypatch):
    """Webhooks accept requests signed with a test token, as Twilio would sign them."""
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "twilio_auth_token", TWILIO_TOKEN)
    monkeypatch.setattr(get_settings(), "public_base_url", PUBLIC_URL)
    monkeypatch.setattr(get_settings(), "twilio_from_number", "+15550000000")
    monkeypatch.setattr(get_settings(), "call_style", "menu")  # the keypad-menu tests; conversation tests switch
    monkeypatch.setattr(get_settings(), "rumik_api_key", "")  # Twilio's <Say>, unless a test sets Rumik up


def twilio_post(client, path: str, form: dict, token: str = TWILIO_TOKEN):
    """POST a webhook signed exactly the way Twilio signs it."""
    from app.routers.twilio import expected_signature

    signature = expected_signature(PUBLIC_URL + path, form, token)
    return client.post(path, data=form, headers={"X-Twilio-Signature": signature})


def login(client, pin: str) -> dict:
    response = client.post("/auth/pin", json={"pin": pin})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['token']}"}


def make_incident(db, camera_id="cam-webcam", type_="access", severity="high", zone="server-room-door",
                  status="open", minutes_ago=1):
    from datetime import datetime, timedelta, timezone

    from app.models import Incident, TimelineEvent

    started = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    incident = Incident(type=type_, severity=severity, status=status, camera_id=camera_id, zone=zone,
                        started_at=started, confidence=0.9, snapshot_path=None, clip_path=None)
    db.add(incident)
    db.flush()
    db.add(TimelineEvent(incident_id=incident.id, at=started, action="detected"))
    db.commit()
    return incident.id


def check_in(db, name: str, action: str = "in") -> None:
    """Put a demo guard on (or off) duty, as a QR scan would."""
    from sqlalchemy import select

    from app.models import User
    from app.services import duty

    user = db.scalar(select(User).where(User.name == name))
    duty.set_duty(db, user, action, "dashboard", "test")
    db.commit()
