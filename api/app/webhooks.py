"""Webhook signatures, Stripe-compatible: `t=<unix>,v1=<hex>` where
v1 = HMAC-SHA256(secret, f"{t}.{payload}") (same construction as stripe-python)."""

import hashlib
import hmac
import time

SIGNATURE_HEADER = "Cleartill-Signature"
DEFAULT_TOLERANCE = 300


class SignatureError(Exception):
    pass


def _hmac(secret: str, timestamp: int | str, payload: str) -> str:
    message = f"{timestamp}.{payload}".encode()
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def sign(secret: str, timestamp: int, payload: str) -> str:
    return f"t={timestamp},v1={_hmac(secret, timestamp, payload)}"


def verify(
    payload: str,
    header: str,
    secret: str,
    tolerance: int = DEFAULT_TOLERANCE,
    now: int | None = None,
) -> bool:
    """Accept any matching v1 (several during secret rotation); ignore other schemes."""
    timestamp, signatures = None, []
    for item in (header or "").split(","):
        key, _, value = item.strip().partition("=")
        if key == "t":
            timestamp = value
        elif key == "v1" and value:
            signatures.append(value)
    if not timestamp or not timestamp.isdigit() or not signatures:
        raise SignatureError("unable to parse the signature header")
    expected = _hmac(secret, timestamp, payload)
    if not any(hmac.compare_digest(expected, sig) for sig in signatures):
        raise SignatureError("no signature matches the expected one")
    now = int(time.time()) if now is None else now
    if abs(now - int(timestamp)) > tolerance:
        raise SignatureError("timestamp outside the tolerance zone")
    return True
