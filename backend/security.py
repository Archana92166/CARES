"""Password and session security helpers using Python standard library APIs."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from .db import Database, utc_now


PBKDF2_ITERATIONS = 600_000


def hash_password(password: str) -> str:
    if not isinstance(password, str) or not password:
        raise ValueError("Password is required.")
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS
    )
    encode = lambda value: base64.urlsafe_b64encode(value).decode("ascii")
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${encode(salt)}${encode(digest)}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        scheme, iterations, salt_text, digest_text = encoded.split("$", 3)
        if scheme != "pbkdf2_sha256":
            return False
        salt = base64.urlsafe_b64decode(salt_text.encode("ascii"))
        expected = base64.urlsafe_b64decode(digest_text.encode("ascii"))
        actual = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), salt, int(iterations)
        )
        return hmac.compare_digest(actual, expected)
    except (TypeError, ValueError, UnicodeError):
        return False


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class SessionStore:
    def __init__(self, database: Database, ttl_seconds: int = 86_400) -> None:
        self.database = database
        self.ttl_seconds = ttl_seconds

    def create(self, user_id: int) -> str:
        token = secrets.token_urlsafe(32)
        created = datetime.now(timezone.utc)
        expires = created + timedelta(seconds=self.ttl_seconds)
        self.database.execute(
            "INSERT INTO sessions(user_id, token_hash, created_at, expires_at) VALUES (?, ?, ?, ?)",
            (user_id, _token_hash(token), created.isoformat(), expires.isoformat()),
        )
        return token

    def resolve(self, token: Optional[str]) -> Optional[int]:
        if not token:
            return None
        row = self.database.fetch_one(
            "SELECT user_id, expires_at FROM sessions "
            "WHERE token_hash = ? AND revoked_at IS NULL",
            (_token_hash(token),),
        )
        if row is None:
            return None
        try:
            if datetime.fromisoformat(row["expires_at"]) <= datetime.now(timezone.utc):
                return None
        except ValueError:
            return None
        return int(row["user_id"])

    def revoke(self, token: Optional[str]) -> None:
        if token:
            self.database.execute(
                "UPDATE sessions SET revoked_at = ? WHERE token_hash = ?",
                (utc_now(), _token_hash(token)),
            )
