"""POST /broadcast: play a recorded voice message to every on-shift guard in one zone."""

import logging
import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool

from app.config import get_settings, media_root
from app.db import SessionLocal
from app.deps import require
from app.models import User
from app.schemas import BroadcastOut
from app.services.audit import record
from app.services.calls import calls
from app.services.directory import on_shift_contacts

router = APIRouter(tags=["broadcast"])
log = logging.getLogger("safetyai.broadcast")

# Audio types Twilio's <Play> can play.
AUDIO_TYPES = {"audio/mpeg": "mp3", "audio/mp3": "mp3", "audio/wav": "wav", "audio/x-wav": "wav", "audio/wave": "wav"}


def _broadcast(zone: str, filename: str, user: User) -> BroadcastOut:
    with SessionLocal() as db:
        contacts = on_shift_contacts(db, zone=zone)
        record(db, user, "broadcast", target=f"zone:{zone}", detail={"file": filename, "recipients": len(contacts)})
        db.commit()
    queued = [c.name for c in contacts
              if calls.place(kind="broadcast", to_name=c.name, to_phone=c.phone, responder_id=c.responder_id,
                             audio_file=filename) is not None]
    return BroadcastOut(zone=zone, recipients=[c.name for c in contacts], queued=len(queued), calls_mode=calls.mode)


@router.post("/broadcast", response_model=BroadcastOut)
async def broadcast(zone: str = Form(..., min_length=1, max_length=120), audio: UploadFile = File(...),
                    user: User = Depends(require("broadcast"))) -> BroadcastOut:
    extension = AUDIO_TYPES.get((audio.content_type or "").lower())
    if extension is None:
        raise HTTPException(status_code=422, detail="audio must be MP3 or WAV (Twilio can play those)")
    limit = int(get_settings().broadcast_max_mb * 1024 * 1024)
    data = await audio.read(limit + 1)
    if len(data) > limit:
        raise HTTPException(status_code=422, detail=f"audio is larger than {get_settings().broadcast_max_mb} MB")
    if not data:
        raise HTTPException(status_code=422, detail="audio file is empty")
    filename = f"broadcast-{uuid.uuid4().hex}.{extension}"
    (media_root() / filename).write_bytes(data)
    return await run_in_threadpool(_broadcast, zone, filename, user)
