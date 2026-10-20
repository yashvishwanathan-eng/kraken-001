"""Load demo data into the database. Safe to run again: existing rows are updated, not duplicated.

    python -m app.seed

Cameras come from config/cameras.yaml and zones from config/zones.yaml.
"""

import logging
from pathlib import Path

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import BACKEND_DIR, load_cameras
from app.db import SessionLocal
from app.models import Camera, Zone

log = logging.getLogger("safetyai.seed")

ZONES_FILE = BACKEND_DIR / "config" / "zones.yaml"


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


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    with SessionLocal() as db:
        cameras = seed_cameras(db)
        db.flush()  # cameras must exist before zones point at them
        zones = seed_zones(db)
        db.commit()
    log.info("seeded %d cameras and %d zones", cameras, zones)


if __name__ == "__main__":
    main()
