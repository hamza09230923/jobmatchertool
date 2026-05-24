from __future__ import annotations

import copy
import hashlib
import json
import os
import sqlite3
import threading
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional


SCORER_VERSION = os.getenv("SCORER_VERSION", "2026-05-24.strict-jd-v3")

try:
    ANALYZE_CACHE_MAX_ENTRIES = max(0, int(os.getenv("ANALYZE_CACHE_MAX_ENTRIES", "256")))
except ValueError:
    ANALYZE_CACHE_MAX_ENTRIES = 256

try:
    ANALYZE_CACHE_TTL_DAYS = max(0, int(os.getenv("ANALYZE_CACHE_TTL_DAYS", "30")))
except ValueError:
    ANALYZE_CACHE_TTL_DAYS = 30

ANALYZE_CACHE_DB = Path(os.getenv("ANALYZE_CACHE_DB", "analysis_cache.db"))
ANALYZE_CACHE_PERSISTENT = os.getenv("ANALYZE_CACHE_PERSISTENT", "1").strip().lower() not in {
    "0",
    "false",
    "no",
}

_memory_cache: OrderedDict[str, dict] = OrderedDict()
_secondary_memory_cache: OrderedDict[str, dict] = OrderedDict()
_lock = threading.Lock()
_initialized = False


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_text(text: str) -> str:
    return " ".join(str(text or "").split())


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def analyze_cache_key(resume_text: str, job_description: str) -> str:
    payload = "\0".join(
        (
            SCORER_VERSION,
            _normalize_text(resume_text),
            _normalize_text(job_description),
        )
    )
    return _sha256(payload)


def secondary_cache_key(kind: str, payload: dict) -> str:
    normalized_payload = json.dumps(
        payload or {},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )
    return _sha256("\0".join((SCORER_VERSION, str(kind or ""), normalized_payload)))


def _resume_hash(resume_text: str) -> str:
    return _sha256(_normalize_text(resume_text))


def _jd_hash(job_description: str) -> str:
    return _sha256(_normalize_text(job_description))


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(ANALYZE_CACHE_DB), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_cache() -> None:
    global _initialized
    if not ANALYZE_CACHE_PERSISTENT:
        return
    if _initialized:
        return
    with _lock:
        if _initialized:
            return
        conn = _connect()
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS analyze_cache (
                    cache_key        TEXT PRIMARY KEY,
                    scorer_version   TEXT NOT NULL,
                    resume_hash      TEXT NOT NULL,
                    jd_hash          TEXT NOT NULL,
                    response_json    TEXT NOT NULL,
                    created_at       TEXT NOT NULL,
                    last_accessed_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_analyze_cache_version_created "
                "ON analyze_cache(scorer_version, created_at)"
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS secondary_cache (
                    cache_key        TEXT PRIMARY KEY,
                    cache_kind       TEXT NOT NULL,
                    scorer_version   TEXT NOT NULL,
                    response_json    TEXT NOT NULL,
                    created_at       TEXT NOT NULL,
                    last_accessed_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_secondary_cache_kind_version_created "
                "ON secondary_cache(cache_kind, scorer_version, created_at)"
            )
            conn.commit()
            _initialized = True
        finally:
            conn.close()


def _is_expired(created_at: str) -> bool:
    if not ANALYZE_CACHE_TTL_DAYS:
        return False
    try:
        created = datetime.fromisoformat(created_at)
    except ValueError:
        return True
    return created < datetime.now(timezone.utc) - timedelta(days=ANALYZE_CACHE_TTL_DAYS)


def _sanitize_response(response: dict) -> dict:
    cached = copy.deepcopy(response)
    cached.pop("debug", None)
    cached.pop("user", None)
    return cached


def _put_memory(cache_key: str, response: dict) -> None:
    if not ANALYZE_CACHE_MAX_ENTRIES:
        return
    _memory_cache[cache_key] = copy.deepcopy(response)
    _memory_cache.move_to_end(cache_key)
    while len(_memory_cache) > ANALYZE_CACHE_MAX_ENTRIES:
        _memory_cache.popitem(last=False)


def _put_secondary_memory(cache_key: str, response: dict) -> None:
    if not ANALYZE_CACHE_MAX_ENTRIES:
        return
    _secondary_memory_cache[cache_key] = copy.deepcopy(response)
    _secondary_memory_cache.move_to_end(cache_key)
    while len(_secondary_memory_cache) > ANALYZE_CACHE_MAX_ENTRIES:
        _secondary_memory_cache.popitem(last=False)


def get_cached_response(cache_key: str) -> Optional[dict]:
    if not ANALYZE_CACHE_MAX_ENTRIES:
        return None
    with _lock:
        cached = _memory_cache.get(cache_key)
        if cached is not None:
            _memory_cache.move_to_end(cache_key)
            return copy.deepcopy(cached)

    if not ANALYZE_CACHE_PERSISTENT:
        return None

    init_cache()
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT response_json, created_at FROM analyze_cache "
            "WHERE cache_key = ? AND scorer_version = ?",
            (cache_key, SCORER_VERSION),
        ).fetchone()
        if not row:
            return None
        if _is_expired(row["created_at"]):
            conn.execute("DELETE FROM analyze_cache WHERE cache_key = ?", (cache_key,))
            conn.commit()
            return None
        response = json.loads(row["response_json"])
        conn.execute(
            "UPDATE analyze_cache SET last_accessed_at = ? WHERE cache_key = ?",
            (_now(), cache_key),
        )
        conn.commit()
    finally:
        conn.close()

    with _lock:
        _put_memory(cache_key, response)
    return copy.deepcopy(response)


def set_cached_response(cache_key: str, response: dict, resume_text: str = "", job_description: str = "") -> None:
    if not ANALYZE_CACHE_MAX_ENTRIES:
        return
    cached = _sanitize_response(response)
    with _lock:
        _put_memory(cache_key, cached)

    if not ANALYZE_CACHE_PERSISTENT:
        return

    init_cache()
    now = _now()
    conn = _connect()
    try:
        conn.execute(
            """
            INSERT INTO analyze_cache (
                cache_key, scorer_version, resume_hash, jd_hash,
                response_json, created_at, last_accessed_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(cache_key) DO UPDATE SET
                scorer_version = excluded.scorer_version,
                resume_hash = excluded.resume_hash,
                jd_hash = excluded.jd_hash,
                response_json = excluded.response_json,
                last_accessed_at = excluded.last_accessed_at
            """,
            (
                cache_key,
                SCORER_VERSION,
                _resume_hash(resume_text),
                _jd_hash(job_description),
                json.dumps(cached, separators=(",", ":"), ensure_ascii=False),
                now,
                now,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def get_cached_secondary_response(cache_key: str, kind: str) -> Optional[dict]:
    if not ANALYZE_CACHE_MAX_ENTRIES:
        return None
    with _lock:
        cached = _secondary_memory_cache.get(cache_key)
        if cached is not None:
            _secondary_memory_cache.move_to_end(cache_key)
            return copy.deepcopy(cached)

    if not ANALYZE_CACHE_PERSISTENT:
        return None

    init_cache()
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT response_json, created_at FROM secondary_cache "
            "WHERE cache_key = ? AND cache_kind = ? AND scorer_version = ?",
            (cache_key, kind, SCORER_VERSION),
        ).fetchone()
        if not row:
            return None
        if _is_expired(row["created_at"]):
            conn.execute("DELETE FROM secondary_cache WHERE cache_key = ?", (cache_key,))
            conn.commit()
            return None
        response = json.loads(row["response_json"])
        conn.execute(
            "UPDATE secondary_cache SET last_accessed_at = ? WHERE cache_key = ?",
            (_now(), cache_key),
        )
        conn.commit()
    finally:
        conn.close()

    with _lock:
        _put_secondary_memory(cache_key, response)
    return copy.deepcopy(response)


def set_cached_secondary_response(cache_key: str, kind: str, response: dict) -> None:
    if not ANALYZE_CACHE_MAX_ENTRIES:
        return
    cached = _sanitize_response(response)
    with _lock:
        _put_secondary_memory(cache_key, cached)

    if not ANALYZE_CACHE_PERSISTENT:
        return

    init_cache()
    now = _now()
    conn = _connect()
    try:
        conn.execute(
            """
            INSERT INTO secondary_cache (
                cache_key, cache_kind, scorer_version, response_json,
                created_at, last_accessed_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(cache_key) DO UPDATE SET
                cache_kind = excluded.cache_kind,
                scorer_version = excluded.scorer_version,
                response_json = excluded.response_json,
                last_accessed_at = excluded.last_accessed_at
            """,
            (
                cache_key,
                kind,
                SCORER_VERSION,
                json.dumps(cached, separators=(",", ":"), ensure_ascii=False),
                now,
                now,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def debug_metadata(cache_key: str, cache_hit: bool) -> dict:
    return {
        "hit": cache_hit,
        "key": cache_key,
        "max_entries": ANALYZE_CACHE_MAX_ENTRIES,
        "persistent": ANALYZE_CACHE_PERSISTENT,
        "scorer_version": SCORER_VERSION,
    }


def status_metadata() -> dict:
    with _lock:
        entries = len(_memory_cache)
        secondary_entries = len(_secondary_memory_cache)
    return {
        "max_entries": ANALYZE_CACHE_MAX_ENTRIES,
        "memory_entries": entries,
        "secondary_memory_entries": secondary_entries,
        "persistent": ANALYZE_CACHE_PERSISTENT,
        "db_path": str(ANALYZE_CACHE_DB),
        "ttl_days": ANALYZE_CACHE_TTL_DAYS,
        "scorer_version": SCORER_VERSION,
    }
