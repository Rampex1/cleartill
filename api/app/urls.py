import ipaddress
import os
import re
import socket
import threading
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from urllib.parse import urlsplit

from app.errors import ApiError

MAX_URL_LENGTH = 2048
# Whitespace/control chars, plus characters browsers and Python disagree on (\\) or that are
# dangerous if the URL is ever echoed into HTML ("'<>).
_FORBIDDEN = re.compile(r"[\s\x00-\x1f\x7f\\\"'<>]")


def validate_redirect_url(url: str, param: str) -> str:
    """Only absolute https URLs (http too when ALLOW_INSECURE_URLS=1, local dev).

    Blocks javascript:, data: and friends, which would otherwise run on our origin when the
    checkout page redirects (design §5.10)."""
    schemes = {"https"} | ({"http"} if os.environ.get("ALLOW_INSECURE_URLS") == "1" else set())
    try:
        parts = urlsplit(url)
        host = parts.hostname
    except ValueError:
        parts, host = None, None
    if (
        len(url) > MAX_URL_LENGTH
        or _FORBIDDEN.search(url)
        or parts is None
        or parts.scheme not in schemes
        or not host
        or parts.username is not None
    ):
        raise ApiError(
            400,
            "invalid_request_error",
            f"Invalid URL for {param}: it must be an absolute https URL.",
            code="url_invalid",
            param=param,
        )
    return url


RESOLVE_TIMEOUT_SECONDS = 5
RESOLVER_THREADS = 4
_resolver_pool = ThreadPoolExecutor(max_workers=RESOLVER_THREADS, thread_name_prefix="resolve")
# Never queue lookups: a merchant pointing at a DNS server that never answers must not starve
# everyone else's validation. Busy means "try again". The webhook sender has its own pool.
_resolver_slots = threading.BoundedSemaphore(RESOLVER_THREADS)
_sender_pool = ThreadPoolExecutor(max_workers=RESOLVER_THREADS, thread_name_prefix="resolve-send")
_sender_slots = threading.BoundedSemaphore(RESOLVER_THREADS)
_NAT64 = ipaddress.IPv6Network("64:ff9b::/96")
_IPV4_COMPATIBLE = ipaddress.IPv6Network("::/96")
_NUMERIC_HOST = re.compile(r"[0-9a-fA-FxX.]+")


def _resolve(host: str, port: int) -> list[str]:
    """Every address the host resolves to (tests monkeypatch this)."""
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return sorted({info[4][0] for info in infos})


def _is_public(ip: str) -> bool:
    addr = ipaddress.ip_address(ip)
    candidates = [addr]
    if isinstance(addr, ipaddress.IPv6Address):
        if addr in _IPV4_COMPATIBLE:
            return False  # ::a.b.c.d is tunnelled to the IPv4 address on some hosts
        # Addresses that embed an IPv4 address must be checked as that address too.
        if addr.ipv4_mapped:
            candidates.append(addr.ipv4_mapped)
        if addr.sixtofour:
            candidates.append(addr.sixtofour)
        if addr.teredo:
            candidates.extend(addr.teredo)
        if addr in _NAT64:
            candidates.append(ipaddress.IPv4Address(addr.packed[-4:]))
    return all(c.is_global and not c.is_multicast for c in candidates)


def _lookup(host: str, port: int, bad: ApiError, for_sender: bool = False) -> list[str]:
    pool, slots = (_sender_pool, _sender_slots) if for_sender else (_resolver_pool, _resolver_slots)
    if not slots.acquire(blocking=False):
        raise ApiError(
            400,
            "invalid_request_error",
            "The webhook URL's host couldn't be checked right now; try again.",
            code="url_invalid",
            param="url",
        )
    future = None
    try:
        future = pool.submit(_resolve, host, port)
        return future.result(timeout=RESOLVE_TIMEOUT_SECONDS)
    except (OSError, FutureTimeout, UnicodeError):
        raise bad from None
    finally:
        if future is not None:
            future.cancel()
        slots.release()


def validate_webhook_url(url: str, *, for_sender: bool = False) -> list[str]:
    """SSRF guard for webhook endpoints (design §5.12). Returns the vetted IPs, which the
    sender pins to (M14). Rejects anything that isn't a public http(s) destination.

    Call it before opening the request transaction: a lookup can take 5 seconds."""
    bad = ApiError(
        400,
        "invalid_request_error",
        "Invalid webhook URL: it must be a public http or https URL.",
        code="url_invalid",
        param="url",
    )
    try:
        parts = urlsplit(url)
        host, port = parts.hostname, parts.port
    except ValueError:
        raise bad from None
    if (
        len(url) > MAX_URL_LENGTH
        or _FORBIDDEN.search(url)
        or parts.scheme not in ("http", "https")
        or not host
        or parts.username is not None
        or port == 0
    ):
        raise bad
    port = port or (443 if parts.scheme == "https" else 80)
    try:
        ips = [str(ipaddress.ip_address(host))]
    except ValueError:
        if _NUMERIC_HOST.fullmatch(host) and any(ch.isdigit() for ch in host):
            # Octal/hex/short IPv4 spellings parse differently across resolvers: refuse them.
            raise bad from None
        ips = _lookup(host, port, bad, for_sender)
    if os.environ.get("ALLOW_INSECURE_URLS") == "1":
        return ips  # local development only: private addresses allowed
    try:
        if not ips or not all(_is_public(ip) for ip in ips):
            raise bad
    except ValueError:
        raise bad from None
    return ips
