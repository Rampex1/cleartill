"""Startup-checked secrets. SECRET_KEY protects stored card fingerprints and idempotency
request hashes, so it must be long and random; each use gets its own derived subkey."""

import hashlib
import hmac
import os
from functools import cache
from urllib.parse import urlsplit

MIN_SECRET_LENGTH = 32


@cache
def secret_key() -> bytes:
    value = os.environ.get("SECRET_KEY", "")
    if len(value) < MIN_SECRET_LENGTH:
        raise RuntimeError(
            f"SECRET_KEY must be set to a random value of at least {MIN_SECRET_LENGTH} characters"
        )
    return value.encode()


@cache
def subkey(purpose: str) -> bytes:
    """A key per purpose, so the fingerprint key isn't also the idempotency key."""
    return hmac.new(secret_key(), purpose.encode(), hashlib.sha256).digest()


def keyed_hash(purpose: str, message: str) -> str:
    return hmac.new(subkey(purpose), message.encode(), hashlib.sha256).hexdigest()


LOCAL_HOSTS = {"localhost", "127.0.0.1", "testserver"}


@cache
def public_base_url() -> str:
    """Absolute base for URLs we hand out (next_action, checkout url). They can carry a
    client_secret, so plain http is only allowed for local hosts."""
    # Render sets RENDER_EXTERNAL_URL to the service's own https URL, so a Render deploy needs
    # no configuration here; an explicit PUBLIC_BASE_URL (e.g. a custom domain) wins.
    value = (
        os.environ.get("PUBLIC_BASE_URL") or os.environ.get("RENDER_EXTERNAL_URL") or ""
    ).rstrip("/")
    parts = urlsplit(value)
    insecure_ok = parts.hostname in LOCAL_HOSTS or os.environ.get("ALLOW_INSECURE_URLS") == "1"
    if not parts.hostname or parts.scheme not in ({"https", "http"} if insecure_ok else {"https"}):
        raise RuntimeError("PUBLIC_BASE_URL must be set to an https URL (http only for localhost)")
    return value


def warn_if_insecure_urls(log) -> None:
    if os.environ.get("ALLOW_INSECURE_URLS") == "1":
        log.warning("ALLOW_INSECURE_URLS=1: the webhook SSRF guard is OFF (local development only)")


def run_worker_in_process() -> bool:
    """Hosts without a free background-worker tier (Render free) run the worker as a thread
    of the web process. Safe with several processes too: deliveries use fenced claims and
    housekeeping jobs lock what they touch."""
    return os.environ.get("RUN_WORKER_IN_PROCESS") == "1"
