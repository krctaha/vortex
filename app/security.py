"""Sifre ve oturum. argon2 varsa onu, yoksa PBKDF2-SHA256 kullanir."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from typing import Any, Dict, Optional

from .config import settings

try:
    from argon2 import PasswordHasher
    from argon2.exceptions import VerifyMismatchError, VerificationError, InvalidHashError
    _ph = PasswordHasher()
    _HAS_ARGON = True
except Exception:  # noqa: BLE001
    _HAS_ARGON = False

PBKDF2_ROUNDS = 260_000
SESSION_MAX_AGE = 60 * 60 * 24 * 14   # 14 gun


def hash_password(password: str) -> str:
    if _HAS_ARGON:
        return _ph.hash(password)
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ROUNDS)
    return f"pbkdf2${PBKDF2_ROUNDS}${base64.b64encode(salt).decode()}${base64.b64encode(dk).decode()}"


def verify_password(password: str, stored: str) -> bool:
    if not stored:
        return False
    if stored.startswith("pbkdf2$"):
        try:
            _, rounds, salt_b64, dk_b64 = stored.split("$")
            dk = hashlib.pbkdf2_hmac("sha256", password.encode(),
                                     base64.b64decode(salt_b64), int(rounds))
            return hmac.compare_digest(dk, base64.b64decode(dk_b64))
        except Exception:  # noqa: BLE001
            return False
    if _HAS_ARGON:
        try:
            return _ph.verify(stored, password)
        except Exception:  # noqa: BLE001
            return False
    return False


def _sign(payload: bytes) -> str:
    sig = hmac.new(settings.secret_key.encode(), payload, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(sig).decode().rstrip("=")


def make_session(data: Dict[str, Any]) -> str:
    body = dict(data)
    body["iat"] = int(time.time())
    raw = json.dumps(body, separators=(",", ":")).encode()
    b64 = base64.urlsafe_b64encode(raw).decode().rstrip("=")
    return f"{b64}.{_sign(raw)}"


def read_session(token: Optional[str]) -> Optional[Dict[str, Any]]:
    if not token or "." not in token:
        return None
    b64, sig = token.rsplit(".", 1)
    try:
        raw = base64.urlsafe_b64decode(b64 + "=" * (-len(b64) % 4))
    except Exception:  # noqa: BLE001
        return None
    if not hmac.compare_digest(_sign(raw), sig):
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if int(time.time()) - int(data.get("iat", 0)) > SESSION_MAX_AGE:
        return None
    return data


def mask_secret(value: str, keep: int = 4) -> str:
    if not value:
        return ""
    if len(value) <= keep * 2:
        return "•" * len(value)
    return f"{value[:keep]}{'•' * 8}{value[-keep:]}"
