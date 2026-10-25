"""Encryption for guard personal details, and badge lookups that do not reveal the badge number.

personal details -> JSON -> Fernet (AES-128-CBC + HMAC-SHA256, authenticated) with a key from DATA_KEY.
badge number     -> HMAC-SHA256 with the same key -> stored as badge_hash, used to find the guard on a scan.

If DATA_KEY is not set, saving personal details is refused (503). Nothing is ever stored in plain text.
Generate a key once with:  python -c "from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())"
"""

import base64
import hashlib
import hmac
import json

from cryptography.fernet import Fernet, InvalidToken
from fastapi import HTTPException

from app.config import get_settings

PERSONAL_FIELDS = ("badge_number", "badge_id", "security_number", "emergency_contact")


def _key() -> bytes:
    raw = get_settings().data_key
    if not raw:
        raise HTTPException(status_code=503, detail="DATA_KEY is not set in .env; personal details cannot be saved")
    return raw.encode()


def encrypt_personal(details: dict[str, str]) -> bytes:
    clean = {field: str(details.get(field, "") or "") for field in PERSONAL_FIELDS}
    return Fernet(_key()).encrypt(json.dumps(clean).encode())


def decrypt_personal(token: bytes | None) -> dict[str, str]:
    if not token:
        return {field: "" for field in PERSONAL_FIELDS}
    try:
        return json.loads(Fernet(_key()).decrypt(token).decode())
    except (InvalidToken, ValueError):
        raise HTTPException(status_code=500, detail="personal details could not be decrypted: check DATA_KEY")


def badge_hash(badge_number: str) -> str:
    """Same badge number always gives the same hash, so a scan can find the guard."""
    digest = hmac.new(_key(), f"badge:{badge_number.strip().upper()}".encode(), hashlib.sha256).digest()
    return base64.b16encode(digest).decode().lower()
