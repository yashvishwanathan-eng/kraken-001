"""PIN hashing, login sessions, camera scope and signed stream tokens.

Permissions are data, not code: a user has a role label, a list of cameras and a list of actions.
Code never checks the role name. Admin sees every camera because their actions include "all_cameras".
"""

import hashlib
import hmac
import logging
import secrets
import time
from datetime import datetime, timedelta, timezone

import bcrypt
from sqlalchemy import delete, select
from sqlalchemy.orm import Session as DbSession

from app.config import get_settings
from app.models import Session, User

log = logging.getLogger("safetyai.permissions")

# Every action a user can be granted. Endpoints check these, never role names.
ACTIONS = {
    "all_cameras",  # see every camera, including ones added later
    "ack",  # acknowledge incidents
    "resolve",  # resolve or mark false alarm
    "manage_users",  # /users
    "view_audit",  # /audit-log
    "manage_zones",  # POST/PUT /zones
    "manage_responders",  # /responders writes
    "alert_all",  # "alert everybody"
    "broadcast",  # /broadcast
    "assistant",  # /assistant/*
    "manage_sites",  # sites, buildings, areas, camera placement
    "manage_guards",  # guard post, equipment and personal details (saving)
    "view_guard_details",  # guard personal details (Admin only by default)
    "scan_badges",  # badge reader or laptop
    "delegate_cameras",  # create and edit users, but only with cameras and actions this user already has
}

BCRYPT_ROUNDS = 10  # about 70 ms per check: fine for a login, slow for someone guessing PINs
STREAM_SIG_LEN = 32

_runtime_stream_secret = secrets.token_bytes(32)


# ---------- PINs ----------

def hash_pin(pin: str) -> str:
    return bcrypt.hashpw(pin.encode(), bcrypt.gensalt(rounds=BCRYPT_ROUNDS)).decode()


def verify_pin(pin: str, pin_hash: str) -> bool:
    return bcrypt.checkpw(pin.encode(), pin_hash.encode())


def find_user_by_pin(db: DbSession, pin: str, exclude_user_id: int | None = None) -> User | None:
    """PIN-only login: check the PIN against every active user's hash (a handful of users)."""
    users = db.scalars(select(User).where(User.active.is_(True))).all()
    return next((u for u in users if u.id != exclude_user_id and verify_pin(pin, u.pin_hash)), None)


# ---------- sessions (opaque tokens) ----------

def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(db: DbSession, user: User) -> str:
    """Make a random token, store only its hash, return the token (shown to the browser once)."""
    token = secrets.token_urlsafe(32)
    expires = datetime.now(timezone.utc) + timedelta(hours=get_settings().session_ttl_h)
    db.execute(delete(Session).where(Session.user_id == user.id, Session.expires_at < datetime.now(timezone.utc)))
    db.add(Session(token_hash=_token_hash(token), user_id=user.id, expires_at=expires))
    return token


def user_for_token(db: DbSession, token: str) -> User | None:
    if not token:
        return None
    session = db.scalar(select(Session).where(Session.token_hash == _token_hash(token)))
    if session is None or session.expires_at < datetime.now(timezone.utc) or not session.user.active:
        return None
    return session.user


def end_sessions(db: DbSession, user_id: int) -> None:
    """Log a user out everywhere (used when they are deactivated or their PIN is reset)."""
    db.execute(delete(Session).where(Session.user_id == user_id))


# ---------- camera scope ----------

def allowed_cameras(user: User) -> set[str] | None:
    """Camera ids this user may see, or None for every camera."""
    return None if "all_cameras" in user.actions else set(user.camera_ids)


def can_see(user: User, camera_id: str) -> bool:
    scope = allowed_cameras(user)
    return scope is None or camera_id in scope


def can(user: User, action: str) -> bool:
    return action in user.actions


# ---------- signed stream tokens ----------

def _stream_secret() -> bytes:
    configured = get_settings().stream_secret
    return configured.encode() if configured else _runtime_stream_secret


def _stream_sig(user_id: int, camera_id: str, exp: int) -> str:
    message = f"{user_id}.{camera_id}.{exp}".encode()
    return hmac.new(_stream_secret(), message, hashlib.sha256).hexdigest()[:STREAM_SIG_LEN]


def sign_stream_token(user_id: int, camera_id: str) -> str:
    exp = int(time.time() + get_settings().stream_token_ttl_s)
    return f"{user_id}.{exp}.{_stream_sig(user_id, camera_id, exp)}"


def check_stream_token(st: str, camera_id: str) -> int | None:
    """Return the user id if the token is genuine, for this camera, and not expired."""
    try:
        user_id_text, exp_text, sig = st.split(".")
        user_id, exp = int(user_id_text), int(exp_text)
    except ValueError:
        return None
    if not hmac.compare_digest(sig, _stream_sig(user_id, camera_id, exp)) or time.time() > exp:
        return None
    return user_id


def stream_url(user_id: int, camera_id: str) -> str:
    return f"/stream/{camera_id}?st={sign_stream_token(user_id, camera_id)}"


if not get_settings().stream_secret:
    log.warning("STREAM_SECRET is not set; using a random one, so stream URLs stop working after a restart")


# ---------- signed share links (snapshots on WhatsApp, broadcast audio for Twilio) ----------

def _share_sig(name: str, exp: int) -> str:
    return hmac.new(_stream_secret(), f"share.{name}.{exp}".encode(), hashlib.sha256).hexdigest()[:STREAM_SIG_LEN]


def sign_share(name: str) -> str:
    """Token for /share/media/<name>: lets an outside service (WhatsApp, Twilio) fetch one file for a while."""
    exp = int(time.time() + get_settings().share_link_ttl_h * 3600)
    return f"{exp}.{_share_sig(name, exp)}"


def check_share(name: str, st: str) -> bool:
    try:
        exp_text, sig = st.split(".")
        exp = int(exp_text)
    except ValueError:
        return False
    return hmac.compare_digest(sig, _share_sig(name, exp)) and time.time() <= exp


def share_url(name: str) -> str | None:
    """Full public link to a media file, or None when PUBLIC_BASE_URL is not set."""
    base = get_settings().public_base_url.rstrip("/")
    return f"{base}/share/media/{name}?st={sign_share(name)}" if base else None
