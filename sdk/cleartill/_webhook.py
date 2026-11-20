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
        """Return the event if `sig_header` is a valid signature of the raw `payload` made within
        `tolerance` seconds of now (either direction, like the server's verifier). Pass the raw
        request body: re-serialised JSON won't match. Any bad input raises
        SignatureVerificationError, never another exception."""
        raw = payload if isinstance(payload, bytes) else payload.encode()
        timestamp, signatures = None, []
        for part in (sig_header or "").split(","):
            key, _, value = part.strip().partition("=")
            if key == "t" and value.isascii() and value.isdigit():
                timestamp = int(value)
            elif key == "v1" and value.isascii():
                signatures.append(value.encode())
        if timestamp is None or not signatures:
            raise SignatureVerificationError("Malformed signature header.")
        expected = hmac.new(secret.encode(), f"{timestamp}.".encode() + raw, hashlib.sha256)
        digest = expected.hexdigest().encode()
        if not any(hmac.compare_digest(digest, s) for s in signatures):
            raise SignatureVerificationError("No signature matches the payload.")
        current = int(time.time()) if now is None else now
        if abs(current - timestamp) > tolerance:
            raise SignatureVerificationError("The signature's timestamp is outside the tolerance.")
        try:
            return json.loads(raw)
        except ValueError as exc:
            raise SignatureVerificationError("The payload is not valid JSON.") from exc
