"""GET /stream/{camera_id}?st=<token>: pass a worker's local MJPEG stream through to the browser.

The <img> tag cannot send a login header, so permission travels in the signed `st` token.
The worker's own port listens on 127.0.0.1 only; this proxy is the only way in from outside.
"""

import logging

import httpx
from fastapi import APIRouter, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask

from app.db import SessionLocal
from app.models import Camera, User
from app.services.permissions import can_see, check_stream_token

router = APIRouter(tags=["stream"])
log = logging.getLogger("safetyai.stream")

WORKER_CONNECT_TIMEOUT_S = 3.0


def _authorise(camera_id: str, st: str) -> int:
    """Return the worker port, or raise 401/403/404."""
    user_id = check_stream_token(st, camera_id)
    if user_id is None:
        raise HTTPException(status_code=401, detail="stream link is invalid or expired; ask for a new one")
    with SessionLocal() as db:
        user = db.get(User, user_id)
        if user is None or not user.active:
            raise HTTPException(status_code=401, detail="stream link belongs to a user who can no longer log in")
        camera = db.get(Camera, camera_id)
        if camera is None:
            raise HTTPException(status_code=404, detail=f"camera {camera_id} not found")
        if not can_see(user, camera_id):
            raise HTTPException(status_code=403, detail=f"camera {camera_id} is not delegated to you")
        return camera.port


@router.get("/stream/{camera_id}")
async def stream(camera_id: str, st: str = Query(..., description="signed stream token from /cameras")):
    port = await run_in_threadpool(_authorise, camera_id, st)
    client = httpx.AsyncClient(timeout=httpx.Timeout(WORKER_CONNECT_TIMEOUT_S, read=None))
    request = client.build_request("GET", f"http://127.0.0.1:{port}/stream")
    try:
        upstream = await client.send(request, stream=True)
    except httpx.HTTPError:
        await client.aclose()
        raise HTTPException(status_code=503, detail=f"camera {camera_id} worker is not running")

    async def close() -> None:
        await upstream.aclose()
        await client.aclose()

    return StreamingResponse(upstream.aiter_raw(), media_type=upstream.headers.get("content-type"),
                             background=BackgroundTask(close))
