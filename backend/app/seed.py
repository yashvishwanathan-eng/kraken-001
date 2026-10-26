"""Load demo data into the database. Safe to run again: existing rows are updated, not duplicated.

    python -m app.seed

Cameras come from config/cameras.yaml, zones from config/zones.yaml and demo users from
config/demo_users.yaml. Demo PINs come from DEMO_USER_PINS in .env (never from git).
"""

import logging
import secrets
from datetime import time
from pathlib import Path

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import BACKEND_DIR, get_settings, load_cameras
from app.db import SessionLocal
from app.models import Camera, Responder, Role, User, Zone
from app.services.permissions import ACTIONS, hash_pin, verify_pin

log = logging.getLogger("safetyai.seed")

ZONES_FILE = BACKEND_DIR / "config" / "zones.yaml"
USERS_FILE = BACKEND_DIR / "config" / "demo_users.yaml"
RESPONDERS_FILE = BACKEND_DIR / "config" / "demo_responders.yaml"


def seed_cameras(db: Session) -> int:
    """Copy cameras.yaml into the database. Each camera gets a number ("camera 6" by voice): the one in the
    yaml, else the number it already has, else the next free one. Cameras added on the dashboard are kept."""
    cameras = load_cameras()
    taken = {n for n in db.scalars(select(Camera.number).where(Camera.number.is_not(None)))}
    for cam in cameras:
        row = db.get(Camera, cam.id) or Camera(id=cam.id)
        row.name, row.source, row.port = cam.name, cam.source, cam.port
        if cam.number is not None and cam.number != row.number:
            other = db.scalar(select(Camera).where(Camera.number == cam.number, Camera.id != cam.id))
            if other is not None:
                other.number = None  # the yaml wins; that camera is renumbered below
                taken.discard(cam.number)
                db.flush()
            row.number = cam.number
            taken.add(cam.number)
        db.add(row)
    db.flush()
    for row in db.scalars(select(Camera).where(Camera.number.is_(None)).order_by(Camera.id)):
        row.number = next(n for n in range(1, 10_000) if n not in taken)
        taken.add(row.number)
    # Never deleted here: deleting a camera would also delete its incidents.
    wanted = {cam.id for cam in cameras}
    for old in db.scalars(select(Camera).where(Camera.id.not_in(wanted), Camera.added_by.is_(None))):
        log.warning("camera %s is in the database but no longer in cameras.yaml (left as it is)", old.id)
    return len(cameras)


def seed_roles(db: Session, users_file: Path = USERS_FILE) -> int:
    """One role template per demo role label, with that user's actions (cameras stay per user).
    Existing roles are left alone, so an admin's edits survive a re-seed."""
    raw = yaml.safe_load(users_file.read_text(encoding="utf-8")) or {}
    count = 0
    for item in raw.get("users", []):
        if db.scalar(select(Role).where(Role.name == item["role"])) is None:
            db.add(Role(name=item["role"], camera_ids=[], actions=sorted(set(item["actions"]))))
            db.flush()
            count += 1
    return count


def seed_zones(db: Session, zones_file: Path = ZONES_FILE) -> int:
    raw = yaml.safe_load(zones_file.read_text(encoding="utf-8")) or {}
    count = 0
    for camera_id, zones in (raw.get("zones") or {}).items():
        names = [item["name"] for item in zones]
        # a zone removed from zones.yaml is removed from the database too (incidents keep their zone name)
        for old in db.scalars(select(Zone).where(Zone.camera_id == camera_id, Zone.name.not_in(names))):
            db.delete(old)
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


def _hhmm(value: str) -> time:
    hours, minutes = value.split(":")
    return time(int(hours), int(minutes))


def seed_responders(db: Session, responders_file: Path = RESPONDERS_FILE,
                    phones: dict[str, str] | None = None) -> list[str]:
    """Phone roster for the call ladder. Phones come from DEMO_RESPONDER_PHONES in .env; anyone without a
    phone there is skipped (so nobody is ever called by accident). Returns lines to print."""
    phones = parse_pins(get_settings().demo_responder_phones) if phones is None else phones
    raw = yaml.safe_load(responders_file.read_text(encoding="utf-8")) or {}
    lines = []
    for item in raw.get("responders", []):
        phone = phones.get(item["user"])
        user = db.scalar(select(User).where(User.name == item["name"]))
        if not phone:
            lines.append(f"{item['name']}: no phone in DEMO_RESPONDER_PHONES, not on the call roster")
            continue
        row = db.scalar(select(Responder).where(Responder.name == item["name"])) or Responder(name=item["name"])
        row.phone, row.language, row.zone = phone, item.get("language", "en"), item["zone"]
        row.shift_start, row.shift_end = _hhmm(item["shift_start"]), _hhmm(item["shift_end"])
        row.call_order, row.active, row.user_id = item.get("call_order", 1), True, user.id if user else None
        db.add(row)
    return lines


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    with SessionLocal() as db:
        cameras = seed_cameras(db)
        db.flush()  # cameras must exist before zones point at them
        zones = seed_zones(db)
        pin_lines = seed_users(db)
        seed_roles(db)
        db.flush()  # users must exist before responders link to them
        pin_lines += seed_responders(db)
        db.commit()
    log.info("seeded %d cameras, %d zones, the demo users and the call roster", cameras, zones)
    for line in pin_lines:
        print(line)


if __name__ == "__main__":
    main()
