"""Startup-checked secrets. SECRET_KEY protects stored card fingerprints and idempotency
request hashes, so it must be long and random; each use gets its own derived subkey."""

import hashlib
import hmac
import os
from functools import cache

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
