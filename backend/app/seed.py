"""Load demo data into the database. Safe to run again: existing rows are updated, not duplicated.

    python -m app.seed

Cameras come from config/cameras.yaml, zones from config/zones.yaml and demo users from
config/demo_users.yaml. Demo PINs come from DEMO_USER_PINS in .env (never from git).
"""

import logging
import secrets
from pathlib import Path

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import BACKEND_DIR, get_settings, load_cameras
from app.db import SessionLocal
from app.models import Camera, User, Zone
from app.services.permissions import ACTIONS, hash_pin, verify_pin

log = logging.getLogger("safetyai.seed")

ZONES_FILE = BACKEND_DIR / "config" / "zones.yaml"
USERS_FILE = BACKEND_DIR / "config" / "demo_users.yaml"


def seed_cameras(db: Session) -> int:
    for cam in load_cameras():
        row = db.get(Camera, cam.id) or Camera(id=cam.id)
        row.name, row.source, row.port = cam.name, cam.source, cam.port
        db.add(row)
    return len(load_cameras())


def seed_zones(db: Session, zones_file: Path = ZONES_FILE) -> int:
    raw = yaml.safe_load(zones_file.read_text(encoding="utf-8")) or {}
    count = 0
    for camera_id, zones in (raw.get("zones") or {}).items():
        for item in zones:
            row = db.scalar(select(Zone).where(Zone.camera_id == camera_id, Zone.name == item["name"]))
            row = row or Zone(camera_id=camera_id, name=item["name"])
            row.kind = item["kind"]
            row.polygon = item["polygon"]
            row.max_people = item.get("max_people")
            db.add(row)
            count += 1
    return count


def parse_pins(text: str) -> dict[str, str]:
    """'anita=1111,vikram=2222' -> {'anita': '1111', 'vikram': '2222'}"""
    pairs = (item.split("=", 1) for item in text.split(",") if "=" in item)
    return {key.strip(): pin.strip() for key, pin in pairs}


def seed_users(db: Session, users_file: Path = USERS_FILE, pins: dict[str, str] | None = None) -> list[str]:
    """Create or update demo users. Returns lines to print (new random PINs are shown once)."""
    pins = parse_pins(get_settings().demo_user_pins) if pins is None else pins
    raw = yaml.safe_load(users_file.read_text(encoding="utf-8")) or {}
    used_pins = set(pins.values())
    lines = []
    for item in raw.get("users", []):
        unknown = set(item["actions"]) - ACTIONS
        if unknown:
            raise ValueError(f"{item['key']}: unknown actions {sorted(unknown)}")
        user = db.scalar(select(User).where(User.name == item["name"]))
        pin = pins.get(item["key"])
        if user is None:
            if pin is None:
                pin = f"{secrets.randbelow(10000):04d}"
                while pin in used_pins:  # PINs must be unique: login is by PIN alone
                    pin = f"{secrets.randbelow(10000):04d}"
                used_pins.add(pin)
                lines.append(f"{item['name']} ({item['role']}): new random PIN {pin}")
            user = User(name=item["name"], pin_hash=hash_pin(pin))
        elif pin is not None and not verify_pin(pin, user.pin_hash):
            user.pin_hash = hash_pin(pin)
        user.role, user.camera_ids, user.actions, user.active = item["role"], item["cameras"], item["actions"], True
        db.add(user)
    return lines


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    with SessionLocal() as db:
        cameras = seed_cameras(db)
        db.flush()  # cameras must exist before zones point at them
        zones = seed_zones(db)
        pin_lines = seed_users(db)
        db.commit()
    log.info("seeded %d cameras, %d zones and the demo users", cameras, zones)
    for line in pin_lines:
        print(line)


if __name__ == "__main__":
    main()
