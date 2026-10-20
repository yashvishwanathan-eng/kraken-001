"""PIN login and the current user.

POST /auth/pin  {pin}  -> {token, user}. The token is opaque: a random string whose hash is in `sessions`.
GET  /me               -> who I am, which cameras I see, which actions I may take.
"""

import collections
import threading
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select

from app.config import get_settings
from app.db import SessionLocal
from app.deps import current_user
from app.models import Camera, User
from app.schemas import LoginResponse, MeResponse, PinLogin, UserBrief
from app.services.audit import record
from app.services.permissions import allowed_cameras, create_session, find_user_by_pin

router = APIRouter(tags=["auth"])

_failures: dict[str, collections.deque[float]] = collections.defaultdict(collections.deque)
_failures_lock = threading.Lock()


def _too_many_failures(client: str) -> bool:
    settings = get_settings()
    now = time.monotonic()
    with _failures_lock:
        recent = _failures[client]
        while recent and now - recent[0] > settings.login_window_s:
            recent.popleft()
        return len(recent) >= settings.login_max_failures


def _note_failure(client: str) -> None:
    with _failures_lock:
        _failures[client].append(time.monotonic())


def reset_login_limits() -> None:
    with _failures_lock:
        _failures.clear()


@router.post("/auth/pin", response_model=LoginResponse)
def login(body: PinLogin, request: Request) -> LoginResponse:
    client = request.client.host if request.client else "unknown"
    if _too_many_failures(client):
        raise HTTPException(status_code=429, detail="too many wrong PINs; wait a minute and try again")
    with SessionLocal() as db:
        user = find_user_by_pin(db, body.pin)
        if user is None:
            _note_failure(client)
            record(db, None, "login_failed", target=client, user_name="unknown")
            db.commit()
            raise HTTPException(status_code=401, detail="wrong PIN")
        token = create_session(db, user)
        record(db, user, "login")
        db.commit()
        return LoginResponse(token=token, user=UserBrief(id=user.id, name=user.name, role=user.role))


@router.get("/me", response_model=MeResponse)
def me(user: User = Depends(current_user)) -> MeResponse:
    scope = allowed_cameras(user)
    if scope is None:
        with SessionLocal() as db:
            camera_ids = list(db.scalars(select(Camera.id).order_by(Camera.id)))
    else:
        camera_ids = sorted(scope)
    return MeResponse(id=user.id, name=user.name, role=user.role, camera_ids=camera_ids, actions=sorted(user.actions))
