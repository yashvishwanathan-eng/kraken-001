"""FastAPI dependencies: "before this endpoint runs, find out who is calling and whether they may".

    @router.post("/events/{id}/ack")
    def ack(user: User = Depends(require("ack"))): ...

Missing or bad token -> 401. Valid token without the action -> 403.
"""

from collections.abc import Callable

from fastapi import Depends, Header, HTTPException

from app.db import SessionLocal
from app.models import User
from app.services.permissions import can, user_for_token


def bearer_token(authorization: str = Header(default="")) -> str:
    scheme, _, token = authorization.partition(" ")
    return token.strip() if scheme.lower() == "bearer" else ""


def current_user(token: str = Depends(bearer_token)) -> User:
    with SessionLocal() as db:
        user = user_for_token(db, token)
        if user is None:
            raise HTTPException(status_code=401, detail="log in first (missing, wrong or expired token)")
        db.expunge(user)  # keep using the loaded user after this session closes
        return user


def require_any(*actions: str) -> Callable[..., User]:
    """Allowed when the user holds at least one of the actions."""
    def check(user: User = Depends(current_user)) -> User:
        if not any(can(user, action) for action in actions):
            raise HTTPException(status_code=403, detail=f"your role is not allowed to do this ({' or '.join(actions)})")
        return user

    return check


def require(action: str) -> Callable[..., User]:
    def check(user: User = Depends(current_user)) -> User:
        if not can(user, action):
            raise HTTPException(status_code=403, detail=f"your role is not allowed to do this ({action})")
        return user

    return check
