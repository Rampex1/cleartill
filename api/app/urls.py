import ipaddress
import os
import re
import socket
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
_resolver_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="resolve")


def _resolve(host: str, port: int) -> list[str]:
    """Every address the host resolves to (tests monkeypatch this)."""
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return sorted({info[4][0] for info in infos})


def _is_public(ip: str) -> bool:
    addr = ipaddress.ip_address(ip)
    candidates = [addr]
    if isinstance(addr, ipaddress.IPv6Address):
        # Addresses that embed an IPv4 address must be checked as that address too.
        if addr.ipv4_mapped:
            candidates.append(addr.ipv4_mapped)
        if addr.sixtofour:
            candidates.append(addr.sixtofour)
        if addr.teredo:
            candidates.extend(addr.teredo)
    return all(c.is_global and not c.is_multicast for c in candidates)


def validate_webhook_url(url: str) -> list[str]:
    """SSRF guard for webhook endpoints (design §5.12). Returns the vetted IPs, which the
    sender pins to (M14). Rejects anything that isn't a public http(s) destination."""
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
    ):
        raise bad
    if os.environ.get("ALLOW_INSECURE_URLS") == "1":
        return []  # local development only: private addresses allowed
    port = port or (443 if parts.scheme == "https" else 80)
    try:
        ips = [str(ipaddress.ip_address(host))]
    except ValueError:
        try:
            future = _resolver_pool.submit(_resolve, host, port)
            ips = future.result(timeout=RESOLVE_TIMEOUT_SECONDS)
        except (OSError, FutureTimeout, UnicodeError):
            raise bad from None
    try:
        if not ips or not all(_is_public(ip) for ip in ips):
            raise bad
    except ValueError:
        raise bad from None
    return ips
