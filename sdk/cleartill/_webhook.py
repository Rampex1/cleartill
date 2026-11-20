"""Verify a webhook's Cleartill-Signature header (same scheme as the server's signer)."""

import hashlib
import hmac
import json
import time

from cleartill._errors import SignatureVerificationError

DEFAULT_TOLERANCE = 300


class Webhook:
    @staticmethod
    def construct_event(
        payload: str | bytes,
        sig_header: str,
        secret: str,
        tolerance: int = DEFAULT_TOLERANCE,
        *,
        now: int | None = None,
    ) -> dict:
        """Return the event if `sig_header` is a valid, fresh signature of the raw `payload`.
        Pass the raw request body: re-serialised JSON won't match."""
        body = payload.decode() if isinstance(payload, bytes) else payload
        timestamp, signatures = None, []
        for part in (sig_header or "").split(","):
            key, _, value = part.strip().partition("=")
            if key == "t" and value.isdigit():
                timestamp = int(value)
            elif key == "v1":
                signatures.append(value)
        if timestamp is None or not signatures:
            raise SignatureVerificationError("Malformed signature header.")
        expected = hmac.new(
            secret.encode(), f"{timestamp}.{body}".encode(), hashlib.sha256
        ).hexdigest()
        if not any(hmac.compare_digest(expected, s) for s in signatures):
            raise SignatureVerificationError("No signature matches the payload.")
        current = int(time.time()) if now is None else now
        if tolerance and current - timestamp > tolerance:
            raise SignatureVerificationError("The signature's timestamp is too old.")
        return json.loads(body)
