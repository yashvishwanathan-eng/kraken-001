"""GET /share/media/{name}?st=...: one media file for an outside service (WhatsApp snapshot, Twilio audio).

Links are signed and expire (SHARE_LINK_TTL_H). Only plain file names in the media folder are served.
"""

import re

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from app.config import media_root
from app.services.permissions import check_share

router = APIRouter(tags=["share"])

SAFE_NAME = re.compile(r"^[A-Za-z0-9_-]+\.(jpg|mp4|mp3|wav)$")


@router.get("/share/media/{name}")
def share_media(name: str, st: str) -> FileResponse:
    if not SAFE_NAME.match(name) or not check_share(name, st):
        raise HTTPException(status_code=403, detail="link is invalid or expired")
    path = media_root() / name
    if not path.is_file():
        raise HTTPException(status_code=404, detail="file not found")
    return FileResponse(path)
