"""Simple in-memory sliding-window rate limiter. No Redis required.

Suitable for single-instance deployments. If you ever go multi-instance, swap
the in-memory dict for Redis (the same Limiter API still works).
"""
from __future__ import annotations

import threading
import time
from collections import defaultdict
from typing import Optional

from fastapi import HTTPException, Request


class _Bucket:
    """Tracks the timestamps of recent hits for one key (IP)."""
    __slots__ = ("timestamps",)

    def __init__(self) -> None:
        self.timestamps: list[float] = []


class RateLimiter:
    """
    Sliding-window limiter. Each key (IP) is allowed `max_hits` actions per
    `window_seconds`. Calls outside the window are pruned on each check.
    """

    def __init__(self, max_hits: int, window_seconds: int) -> None:
        self.max_hits = max_hits
        self.window_seconds = window_seconds
        self._buckets: dict[str, _Bucket] = defaultdict(_Bucket)
        self._lock = threading.Lock()

    def hit(self, key: str) -> tuple[bool, int]:
        """
        Record a hit for this key. Returns (allowed, retry_after_seconds).
        retry_after_seconds is 0 when allowed, else seconds until oldest hit expires.
        """
        now = time.time()
        cutoff = now - self.window_seconds
        with self._lock:
            bucket = self._buckets[key]
            # prune expired timestamps
            bucket.timestamps = [t for t in bucket.timestamps if t > cutoff]
            if len(bucket.timestamps) >= self.max_hits:
                retry_after = int(bucket.timestamps[0] + self.window_seconds - now) + 1
                return False, max(retry_after, 1)
            bucket.timestamps.append(now)
            return True, 0


def client_ip(request: Request) -> str:
    """
    Resolve the real client IP behind Render's proxy.
    Render and most PaaS providers set X-Forwarded-For with the chain
    'client, proxy1, proxy2' — the first value is what we want.
    """
    fwd = request.headers.get("x-forwarded-for", "").strip()
    if fwd:
        return fwd.split(",")[0].strip()
    real_ip = request.headers.get("x-real-ip", "").strip()
    if real_ip:
        return real_ip
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def make_dependency(limiter: RateLimiter, scope: str):
    """
    Build a FastAPI dependency that rate-limits the calling IP.
    `scope` is a human-readable label included in the 429 message.
    """
    def _dep(request: Request) -> None:
        ip = client_ip(request)
        key = f"{scope}:{ip}"
        allowed, retry_after = limiter.hit(key)
        if not allowed:
            raise HTTPException(
                status_code=429,
                detail=f"Too many {scope} attempts. Please try again in {retry_after} seconds.",
                headers={"Retry-After": str(retry_after)},
            )
    return _dep


# ── Pre-configured limiters used by main.py ───────────────────────────────────

# Strict: 5 attempts per minute. Apply to login + signup + forgot-password.
auth_limiter = RateLimiter(max_hits=5, window_seconds=60)
require_auth_rate_limit = make_dependency(auth_limiter, "authentication")

# Slightly looser for scan endpoints if needed in future.
scan_limiter = RateLimiter(max_hits=20, window_seconds=60)
require_scan_rate_limit = make_dependency(scan_limiter, "scan")
