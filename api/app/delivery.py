"""Webhook delivery from the outbox (design §5.9): claim one due delivery (fencing token),
send it outside any transaction, then record the attempt with a fenced update."""

import json
import time
import uuid
from dataclasses import dataclass
from datetime import timedelta
from urllib.parse import urlsplit, urlunsplit

import httpx2
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app import clock, urls, webhooks
from app.db import SessionLocal
from app.errors import ApiError
from app.events import event_json, pending_counts
from app.ids import new_id
from app.models import Event, WebhookAttempt, WebhookDelivery, WebhookEndpoint

BACKOFF = [60, 300, 1800, 7200, 18000, 36000]  # seconds; the last repeats
GIVE_UP_AFTER = timedelta(hours=72)
LOCK_FOR = timedelta(seconds=120)  # > send timeout + read cap, so a live claim never expires
SEND_TIMEOUT_SECONDS = 10
MAX_TOTAL_SECONDS = 15
MAX_RESPONSE_BYTES = 64 * 1024
MAX_ERROR_LENGTH = 500

_CLAIM = text(
    """
    UPDATE webhook_deliveries d SET locked_until = :lock, claim_id = :claim_id
    WHERE d.id = (
        SELECT d2.id FROM webhook_deliveries d2
        JOIN webhook_endpoints e ON e.id = d2.endpoint_id
        WHERE d2.status = 'pending' AND e.status = 'enabled' AND NOT e.deleted
          AND d2.next_attempt_at <= :now
          AND (d2.locked_until IS NULL OR d2.locked_until < :now)
        ORDER BY d2.next_attempt_at
        LIMIT 1
        FOR UPDATE OF d2 SKIP LOCKED
    )
    RETURNING d.id, d.event_id, d.endpoint_id
    """
)


@dataclass(frozen=True)
class Claim:
    delivery_id: str
    claim_id: str
    url: str
    secret: str
    payload: str


@dataclass(frozen=True)
class SendResult:
    status_code: int | None
    error: str | None
    duration_ms: int

    @property
    def ok(self) -> bool:
        return self.status_code is not None and 200 <= self.status_code < 300


def claim(db: Session) -> Claim | None:
    """Claim one due delivery and commit, so no lock is held while sending."""
    now = clock.now()
    claim_id = str(uuid.uuid4())
    row = db.execute(_CLAIM, {"now": now, "lock": now + LOCK_FOR, "claim_id": claim_id}).first()
    if row is None:
        db.commit()
        return None
    endpoint = db.get(WebhookEndpoint, row.endpoint_id)
    event = db.get(Event, row.event_id)
    body = event_json(event, pending_counts(db, [event.id]).get(event.id, 0))
    result = Claim(
        delivery_id=row.id,
        claim_id=claim_id,
        url=endpoint.url,
        secret=endpoint.secret,
        payload=json.dumps(body, separators=(",", ":")),
    )
    db.commit()
    return result


def send(url: str, payload: str, headers: dict) -> SendResult:
    """POST to the endpoint pinned to an address the SSRF guard vetted (design §5.12)."""
    started = time.monotonic()

    def done(status_code: int | None, error: str | None) -> SendResult:
        return SendResult(status_code, error, int((time.monotonic() - started) * 1000))

    try:
        ips = urls.validate_webhook_url(url, for_sender=True)
    except ApiError:
        return done(None, "url_blocked")
    parts = urlsplit(url)
    host = parts.hostname
    port = parts.port or (443 if parts.scheme == "https" else 80)
    ip = ips[0] if ips else host
    netloc = f"[{ip}]:{port}" if ":" in ip else f"{ip}:{port}"
    target = urlunsplit((parts.scheme, netloc, parts.path or "/", parts.query, ""))
    # Connect to the vetted IP; keep the real host for HTTP and TLS (SNI + certificate check).
    extensions = {"sni_hostname": host} if parts.scheme == "https" else {}
    try:
        # A fresh client per send: no pooled connection is reused for another host.
        with httpx2.Client(
            trust_env=False, follow_redirects=False, timeout=SEND_TIMEOUT_SECONDS
        ) as client:
            request = client.build_request(
                "POST",
                target,
                content=payload.encode(),
                headers={**headers, "Host": parts.netloc},
                extensions=extensions,
            )
            response = client.send(request, stream=True)
            try:
                received = 0
                for chunk in response.iter_bytes():
                    received += len(chunk)
                    if received > MAX_RESPONSE_BYTES:
                        break
                    if time.monotonic() - started > MAX_TOTAL_SECONDS:
                        return done(response.status_code, "response_too_slow")
                return done(response.status_code, None)
            finally:
                response.close()
    except httpx2.HTTPError as exc:
        return done(None, f"{type(exc).__name__}: {exc}"[:MAX_ERROR_LENGTH])


def finish(db: Session, claimed: Claim, result: SendResult) -> bool:
    """Record the outcome. Fenced: a stale claim (re-claimed, or already finished) changes
    nothing and returns False."""
    now = clock.now()
    delivery = db.scalar(
        select(WebhookDelivery)
        .where(
            WebhookDelivery.id == claimed.delivery_id,
            WebhookDelivery.claim_id == claimed.claim_id,
            WebhookDelivery.status == "pending",
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if delivery is None:
        db.commit()
        return False
    db.add(
        WebhookAttempt(
            id=new_id("wa_"),
            merchant_id=delivery.merchant_id,
            delivery_id=delivery.id,
            at=now,
            status_code=result.status_code,
            error=result.error[:MAX_ERROR_LENGTH] if result.error else None,
            duration_ms=result.duration_ms,
            manual=False,
        )
    )
    if result.ok:
        delivery.status = "succeeded"
    else:
        delivery.attempts += 1
        if now - delivery.created_at >= GIVE_UP_AFTER:
            delivery.status = "failed"
        else:
            step = BACKOFF[min(delivery.attempts - 1, len(BACKOFF) - 1)]
            delivery.next_attempt_at = now + timedelta(seconds=step)
    delivery.locked_until = None
    db.commit()
    return True


def deliver_one(session_factory=SessionLocal) -> bool:
    """Claim, sign, send, finish one delivery. False when nothing is due."""
    with session_factory() as db:
        claimed = claim(db)
        if claimed is None:
            return False
        timestamp = int(clock.now().timestamp())
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "Cleartill-Webhooks/1.0",
            webhooks.SIGNATURE_HEADER: webhooks.sign(claimed.secret, timestamp, claimed.payload),
        }
        result = send(claimed.url, claimed.payload, headers)
        finish(db, claimed, result)
        return True
