"""Auth primitives: bcrypt password hashing, JWT issue/verify, secure tokens."""
from __future__ import annotations

import logging
import os
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
import jwt
from fastapi import Header, HTTPException

logger = logging.getLogger(__name__)

JWT_SECRET = os.getenv("JWT_SECRET", "")
JWT_ALGO = "HS256"
JWT_TTL_DAYS = int(os.getenv("JWT_TTL_DAYS", "30"))

if not JWT_SECRET:
    # Don't crash, but loudly warn — use a deterministic dev fallback so sessions don't
    # invalidate on every reload during local dev. NEVER ship without JWT_SECRET set.
    logger.warning("JWT_SECRET is not set. Using an insecure dev fallback. Set JWT_SECRET in .env.")
    JWT_SECRET = "dev-only-insecure-secret-replace-me"

EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")
MIN_PASSWORD_LEN = 8


def is_valid_email(email: str) -> bool:
    return bool(EMAIL_RE.match(email or ""))


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def create_jwt(user_id: int, email: str) -> str:
    payload = {
        "sub": str(user_id),
        "email": email,
        "iat": int(datetime.now(timezone.utc).timestamp()),
        "exp": int((datetime.now(timezone.utc) + timedelta(days=JWT_TTL_DAYS)).timestamp()),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGO)


def decode_jwt(token: str) -> Optional[dict]:
    if not token:
        return None
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGO])
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None


def generate_secure_token(nbytes: int = 32) -> str:
    """URL-safe random token for email verification + password reset."""
    return secrets.token_urlsafe(nbytes)


def extract_bearer_token(authorization: Optional[str]) -> Optional[str]:
    """Pull the token out of an 'Authorization: Bearer xxx' header."""
    if not authorization:
        return None
    parts = authorization.split(None, 1)
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1].strip()
    return None


# ── FastAPI dependency ────────────────────────────────────────────────────────

def get_current_user_id(authorization: Optional[str] = Header(None)) -> int:
    """FastAPI dependency: parse Bearer token, return user_id or raise 401."""
    token = extract_bearer_token(authorization)
    payload = decode_jwt(token) if token else None
    if not payload or "sub" not in payload:
        raise HTTPException(status_code=401, detail="Authentication required.")
    try:
        return int(payload["sub"])
    except (TypeError, ValueError):
        raise HTTPException(status_code=401, detail="Invalid session.")


def get_current_user_id_optional(authorization: Optional[str] = Header(None)) -> Optional[int]:
    """Same as above but returns None instead of raising — used when auth is optional."""
    token = extract_bearer_token(authorization)
    payload = decode_jwt(token) if token else None
    if not payload or "sub" not in payload:
        return None
    try:
        return int(payload["sub"])
    except (TypeError, ValueError):
        return None
