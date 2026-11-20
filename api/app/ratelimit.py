"""Per-client-IP attempt limits for login and signup.

ponytail: counters live in this process; move them to Postgres if more than one replica runs."""

import ipaddress
import logging
import os
import threading
import time
from collections import deque

from fastapi import Request

from app.errors import ApiError

log = logging.getLogger("cleartill")

_now = time.monotonic  # tests monkeypatch this
_lock = threading.Lock()
_hits: dict[tuple[str, str], deque] = {}
MAX_TRACKED = 100_000  # bounded memory: beyond this, stale entries are swept


def trusted_hops() -> int:
    raw = os.environ.get("TRUSTED_PROXY_HOPS") or "0"
    try:
        hops = int(raw)
    except ValueError:
        log.error("TRUSTED_PROXY_HOPS=%r is not an integer; ignoring X-Forwarded-For", raw)
        return 0
    return max(hops, 0)


def _bucket_key(ip: str) -> str | None:
    """IPv6 clients are grouped by /64 (one subscriber's block); invalid strings → None."""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return None
    if isinstance(addr, ipaddress.IPv6Address):
        if addr.ipv4_mapped:
            return str(addr.ipv4_mapped)
        return str(ipaddress.ip_network(f"{addr}/64", strict=False))
    return str(addr)


def reset() -> None:
    with _lock:
        _hits.clear()


def client_ip(request: Request) -> str:
    """The client's IP. X-Forwarded-For is trusted only behind a known number of proxies
    (TRUSTED_PROXY_HOPS, e.g. 1 on Railway): the N-th entry from the right is the one our own
    proxy appended. With 0 (the default, e.g. local compose) XFF is ignored entirely, since a
    client can put anything in it."""
    fallback = request.client.host if request.client else "unknown"
    hops = trusted_hops()
    if hops > 0:
        # A proxy may append a second header line instead of extending the first: read all.
        xff = ",".join(request.headers.getlist("x-forwarded-for"))
        parts = [p.strip() for p in xff.split(",") if p.strip()]
        if len(parts) >= hops and _bucket_key(parts[-hops]) is not None:
            return parts[-hops]
    return fallback


def _sweep(now: float, window: float) -> None:
    for key in [k for k, q in _hits.items() if not q or q[-1] <= now - window]:
        del _hits[key]


def check(bucket: str, ip: str, limit: int = 10, window: float = 60.0) -> None:
    now = _now()
    key = (bucket, _bucket_key(ip) or ip[:64])
    with _lock:
        if len(_hits) >= MAX_TRACKED:
            _sweep(now, window)
        hits = _hits.setdefault(key, deque())
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
