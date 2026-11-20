"""Per-client-IP attempt limits for login and signup.

ponytail: counters live in this process; move them to Postgres if more than one replica runs."""

import os
import threading
import time
from collections import deque

from fastapi import Request

from app.errors import ApiError

_now = time.monotonic  # tests monkeypatch this
_lock = threading.Lock()
_hits: dict[tuple[str, str], deque] = {}


def reset() -> None:
    with _lock:
        _hits.clear()


def client_ip(request: Request) -> str:
    """The client's IP. X-Forwarded-For is trusted only behind a known number of proxies
    (TRUSTED_PROXY_HOPS, e.g. 1 on Railway): the N-th entry from the right is the one our own
    proxy appended. With 0 (the default, e.g. local compose) XFF is ignored entirely, since a
    client can put anything in it."""
    hops = int(os.environ.get("TRUSTED_PROXY_HOPS") or 0)
    if hops > 0:
        parts = [p.strip() for p in request.headers.get("x-forwarded-for", "").split(",")]
        parts = [p for p in parts if p]
        if len(parts) >= hops:
            return parts[-hops]
    return request.client.host if request.client else "unknown"


def check(bucket: str, ip: str, limit: int = 10, window: float = 60.0) -> None:
    now = _now()
    with _lock:
        hits = _hits.setdefault((bucket, ip), deque())
        while hits and hits[0] <= now - window:
            hits.popleft()
        if len(hits) >= limit:
            retry_after = max(1, int(hits[0] + window - now) + 1)
            raise ApiError(
                429,
                "invalid_request_error",
                "Too many attempts. Try again later.",
                code="rate_limit",
                headers={"Retry-After": str(retry_after)},
            )
        hits.append(now)
