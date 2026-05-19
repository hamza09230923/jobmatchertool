"""SQLite-backed user store. Single-file DB at users.db (override with USERS_DB env var)."""
from __future__ import annotations

import logging
import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

DB_PATH = Path(os.getenv("USERS_DB", "users.db"))
_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    email                    TEXT    UNIQUE NOT NULL,
    password_hash            TEXT    NOT NULL,
    created_at               TEXT    NOT NULL,
    lifetime_scans           INTEGER NOT NULL DEFAULT 0,
    tier                     TEXT    NOT NULL DEFAULT 'free',
    email_verified           INTEGER NOT NULL DEFAULT 0,
    verification_token       TEXT,
    verification_sent_at     TEXT,
    password_reset_token     TEXT,
    password_reset_expires   TEXT
);
CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);
CREATE INDEX IF NOT EXISTS idx_users_verification_token ON users(verification_token);
CREATE INDEX IF NOT EXISTS idx_users_reset_token ON users(password_reset_token);
"""


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    with _lock:
        conn = _connect()
        try:
            conn.executescript(SCHEMA)
            conn.commit()
        finally:
            conn.close()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── User CRUD ─────────────────────────────────────────────────────────────────

def create_user(email: str, password_hash: str, verification_token: Optional[str] = None) -> dict:
    """Create a new user. Raises sqlite3.IntegrityError if email already exists."""
    with _lock:
        conn = _connect()
        try:
            cur = conn.execute(
                "INSERT INTO users (email, password_hash, created_at, verification_token, verification_sent_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (email.lower(), password_hash, _now(), verification_token, _now() if verification_token else None),
            )
            conn.commit()
            user_id = cur.lastrowid
            return get_user_by_id(user_id)
        finally:
            conn.close()


def get_user_by_email(email: str) -> Optional[dict]:
    conn = _connect()
    try:
        row = conn.execute("SELECT * FROM users WHERE email = ?", (email.lower(),)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_user_by_id(user_id: int) -> Optional[dict]:
    conn = _connect()
    try:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_user_by_verification_token(token: str) -> Optional[dict]:
    if not token:
        return None
    conn = _connect()
    try:
        row = conn.execute("SELECT * FROM users WHERE verification_token = ?", (token,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_user_by_reset_token(token: str) -> Optional[dict]:
    if not token:
        return None
    conn = _connect()
    try:
        row = conn.execute("SELECT * FROM users WHERE password_reset_token = ?", (token,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def mark_email_verified(user_id: int) -> None:
    with _lock:
        conn = _connect()
        try:
            conn.execute(
                "UPDATE users SET email_verified = 1, verification_token = NULL WHERE id = ?",
                (user_id,),
            )
            conn.commit()
        finally:
            conn.close()


def set_verification_token(user_id: int, token: str) -> None:
    with _lock:
        conn = _connect()
        try:
            conn.execute(
                "UPDATE users SET verification_token = ?, verification_sent_at = ? WHERE id = ?",
                (token, _now(), user_id),
            )
            conn.commit()
        finally:
            conn.close()


def set_password_reset_token(user_id: int, token: str, expires_iso: str) -> None:
    with _lock:
        conn = _connect()
        try:
            conn.execute(
                "UPDATE users SET password_reset_token = ?, password_reset_expires = ? WHERE id = ?",
                (token, expires_iso, user_id),
            )
            conn.commit()
        finally:
            conn.close()


def update_password(user_id: int, password_hash: str) -> None:
    with _lock:
        conn = _connect()
        try:
            conn.execute(
                "UPDATE users SET password_hash = ?, password_reset_token = NULL, password_reset_expires = NULL "
                "WHERE id = ?",
                (password_hash, user_id),
            )
            conn.commit()
        finally:
            conn.close()


def increment_lifetime_scans(user_id: int) -> int:
    """Atomically increment lifetime_scans and return the new value."""
    with _lock:
        conn = _connect()
        try:
            conn.execute("UPDATE users SET lifetime_scans = lifetime_scans + 1 WHERE id = ?", (user_id,))
            conn.commit()
            row = conn.execute("SELECT lifetime_scans FROM users WHERE id = ?", (user_id,)).fetchone()
            return int(row["lifetime_scans"]) if row else 0
        finally:
            conn.close()


def set_tier(user_id: int, tier: str) -> None:
    """tier in {'free', 'paid'}."""
    if tier not in ("free", "paid"):
        raise ValueError(f"Invalid tier: {tier}")
    with _lock:
        conn = _connect()
        try:
            conn.execute("UPDATE users SET tier = ? WHERE id = ?", (tier, user_id))
            conn.commit()
        finally:
            conn.close()


def list_users(limit: int = 100) -> list[dict]:
    """Return up to `limit` users for admin dashboards. Newest first."""
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT id, email, created_at, lifetime_scans, tier, email_verified "
            "FROM users ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def delete_user_by_id(user_id: int) -> bool:
    """Permanently delete a user. Returns True if a row was removed."""
    with _lock:
        conn = _connect()
        try:
            cur = conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()


# Eager init so importers don't have to call init_db() manually.
init_db()
