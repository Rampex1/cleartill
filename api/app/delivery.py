"""Webhook delivery from the outbox (design §5.9): claim one due delivery (fencing token),
send it outside any transaction, then record the attempt with a fenced update."""

import ipaddress
import json
import logging
import ssl
import time
import uuid
from dataclasses import dataclass
from datetime import timedelta
from urllib.parse import urlsplit, urlunsplit

import httpcore2
from sqlalchemy import select, text, true, update
from sqlalchemy.orm import Session

from app import clock, urls, webhooks
from app.db import SessionLocal
from app.errors import ApiError
from app.events import event_json, pending_counts
from app.ids import new_id
from app.models import Event, WebhookAttempt, WebhookDelivery, WebhookEndpoint
from app.tenancy import get_owned

log = logging.getLogger("cleartill.delivery")

BACKOFF = [60, 300, 1800, 7200, 18000, 36000]  # seconds; the last repeats
GIVE_UP_AFTER = timedelta(hours=72)
LOCK_SECONDS = 120  # > the whole-send deadline (15 s) + DNS (5 s): a live claim never expires
SEND_TIMEOUT_SECONDS = 10
MAX_TOTAL_SECONDS = 15
MAX_RESPONSE_BYTES = 64 * 1024
MAX_ERROR_LENGTH = 500

_CLAIM = text(
    """
    UPDATE webhook_deliveries d
    SET locked_until = now() + make_interval(secs => :lock_seconds), claim_id = :claim_id
    WHERE d.id = (
        SELECT d2.id FROM webhook_deliveries d2
        JOIN webhook_endpoints e ON e.id = d2.endpoint_id
        WHERE d2.status = 'pending' AND e.status = 'enabled' AND NOT e.deleted
          AND d2.next_attempt_at <= :now
          AND (d2.locked_until IS NULL OR d2.locked_until < now())
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
    """Claim one due delivery and commit, so no lock is held while sending. Lock times use the
    database clock (workers on other hosts agree); due-ness uses the app clock."""
    now = clock.now()
    claim_id = str(uuid.uuid4())
    row = db.execute(
        _CLAIM, {"now": now, "lock_seconds": LOCK_SECONDS, "claim_id": claim_id}
    ).first()
    if row is None:
        db.commit()
        return None
    try:
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
    except Exception:
        # Don't let one unrenderable delivery sit at the head of the queue: back it off.
        log.exception("could not build webhook payload delivery=%s", row.id)
        db.rollback()
        db.execute(
            update(WebhookDelivery)
            .where(WebhookDelivery.id == row.id)
            .values(
                attempts=WebhookDelivery.attempts + 1,
                next_attempt_at=now + timedelta(seconds=BACKOFF[0]),
            )
        )
        db.commit()
        return None
    db.commit()
    return result


_CLAIM_ONE = text(
    """
    UPDATE webhook_deliveries
    SET locked_until = now() + make_interval(secs => :lock_seconds), claim_id = :claim_id
    WHERE id = :id AND (locked_until IS NULL OR locked_until < now())
    RETURNING id, event_id, endpoint_id
    """
)


def claim_manual(db: Session, merchant_id: str, delivery_id: str) -> Claim:
    """Claim one delivery for a dashboard resend (M20), fenced like the worker's claim, then
    commit, so no lock is held during the send. 409 while another send holds the claim."""
    delivery = get_owned(db, WebhookDelivery, delivery_id, merchant_id, for_update=True)
    endpoint = db.get(WebhookEndpoint, delivery.endpoint_id)
    if endpoint.deleted:
        raise ApiError(
            400,
            "invalid_request_error",
            "This delivery's webhook endpoint has been deleted.",
            code="resource_missing",
        )
    claim_id = str(uuid.uuid4())
    row = db.execute(
        _CLAIM_ONE, {"id": delivery.id, "lock_seconds": LOCK_SECONDS, "claim_id": claim_id}
    ).first()
    if row is None:
        db.rollback()
        raise ApiError(
            409,
            "invalid_request_error",
            "This delivery is being sent right now. Try again in a moment.",
            code="delivery_in_progress",
        )
    event = db.get(Event, delivery.event_id)
    body = event_json(event, pending_counts(db, [event.id]).get(event.id, 0))
    db.commit()
    return Claim(
        delivery_id=delivery.id,
        claim_id=claim_id,
        url=endpoint.url,
        secret=endpoint.secret,
        payload=json.dumps(body, separators=(",", ":")),
    )


def signed_headers(claimed: Claim) -> dict:
    timestamp = int(clock.now().timestamp())
    return {
        "Content-Type": "application/json",
        "User-Agent": "Cleartill-Webhooks/1.0",
        webhooks.SIGNATURE_HEADER: webhooks.sign(claimed.secret, timestamp, claimed.payload),
    }


class _DeadlineStream(httpcore2.NetworkStream):
    """Every socket operation gets min(its own timeout, time left before the deadline), so the
    whole exchange (connect, TLS, request, status line, headers, body) is bounded."""

    def __init__(self, inner: httpcore2.NetworkStream, deadline: float):
        self._inner, self._deadline = inner, deadline

    def _left(self, timeout: float | None) -> float:
        remaining = self._deadline - time.monotonic()
        if remaining <= 0:
            raise httpcore2.ReadTimeout("delivery deadline exceeded")
        return remaining if timeout is None else min(timeout, remaining)

    def read(self, max_bytes: int, timeout: float | None = None) -> bytes:
        return self._inner.read(max_bytes, self._left(timeout))

    def write(self, buffer: bytes, timeout: float | None = None) -> None:
        self._inner.write(buffer, self._left(timeout))

    def close(self) -> None:
        self._inner.close()

    def start_tls(self, ssl_context, server_hostname=None, timeout=None):
        stream = self._inner.start_tls(ssl_context, server_hostname, self._left(timeout))
        return _DeadlineStream(stream, self._deadline)

    def get_extra_info(self, info: str):
        return self._inner.get_extra_info(info)


class _DeadlineBackend(httpcore2.NetworkBackend):
    def __init__(self, deadline: float):
        self._inner, self._deadline = httpcore2.SyncBackend(), deadline

    def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        remaining = self._deadline - time.monotonic()
        if remaining <= 0:
            raise httpcore2.ConnectTimeout("delivery deadline exceeded")
        timeout = remaining if timeout is None else min(timeout, remaining)
        stream = self._inner.connect_tcp(host, port, timeout, local_address, socket_options)
        return _DeadlineStream(stream, self._deadline)

    def connect_unix_socket(self, path, timeout=None, socket_options=None):
        raise httpcore2.ConnectError("unix sockets are not allowed")

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)


def _ipv4_first(ips: list[str]) -> list[str]:
    return sorted(ips, key=lambda ip: ipaddress.ip_address(ip).version)


def send(url: str, payload: str, headers: dict) -> SendResult:
    """POST to the endpoint pinned to addresses the SSRF guard vetted (design §5.12).

    Raw httpcore2: no proxies from the environment, no redirects, no decompression (the body
    is read raw and capped), and one deadline over the whole exchange."""
    started = time.monotonic()

    def done(status_code: int | None, error: str | None) -> SendResult:
        return SendResult(status_code, error, int((time.monotonic() - started) * 1000))

    try:
        parts = urlsplit(url)
        host = parts.hostname.encode("idna").decode("ascii")
        ips = urls.validate_webhook_url(url, for_sender=True)
    except (ApiError, UnicodeError, AttributeError):
        return done(None, "url_blocked")
    if not ips:
        return done(None, "url_blocked")
    port = parts.port or (443 if parts.scheme == "https" else 80)
    host_header = host if parts.port is None else f"{host}:{parts.port}"
    deadline = started + MAX_TOTAL_SECONDS
    extensions = {
        "timeout": {key: SEND_TIMEOUT_SECONDS for key in ("connect", "read", "write", "pool")}
    }
    if parts.scheme == "https":
        extensions["sni_hostname"] = host  # TLS checks the certificate against the real host
    request_headers = [
        ("Host", host_header),
        ("Content-Length", str(len(payload.encode()))),
        *headers.items(),
    ]
    last_error = "connect_error"
    try:
        for ip in _ipv4_first(ips):
            netloc = f"[{ip}]:{port}" if ":" in ip else f"{ip}:{port}"
            target = urlunsplit((parts.scheme, netloc, parts.path or "/", parts.query, ""))
            # A fresh pool per address and per send: no connection is reused for another host.
            with httpcore2.ConnectionPool(
                ssl_context=ssl.create_default_context(),
                max_keepalive_connections=0,
                network_backend=_DeadlineBackend(deadline),
            ) as pool:
                try:
                    with pool.stream(
                        "POST",
                        target,
                        headers=request_headers,
                        content=payload.encode(),
                        extensions=extensions,
                    ) as response:
                        received = 0
                        for chunk in response.iter_stream():
                            received += len(chunk)
                            if received > MAX_RESPONSE_BYTES:
                                break
                        return done(response.status, None)
                except httpcore2.ConnectError:
                    last_error = "connect_error"
                    continue  # try the next vetted address
    except httpcore2.TimeoutException:
        return done(None, "timeout")
    except httpcore2.NetworkError:
        return done(None, "network_error")
    except Exception:
        log.exception("unexpected webhook send failure")
        return done(None, "send_error")
    return done(None, last_error)


def finish(db: Session, claimed: Claim, result: SendResult, *, manual: bool = False) -> bool:
    """Record the outcome. Fenced: a stale claim (re-claimed, or already finished) changes
    nothing and returns False. A manual resend (M20) logs its attempt and can succeed, but
    leaves the retry schedule alone when it fails.

    Departure from design §5.9: a fenced-out finish writes no attempt row."""
    now = clock.now()
    delivery = db.scalar(
        select(WebhookDelivery)
        .where(
            WebhookDelivery.id == claimed.delivery_id,
            WebhookDelivery.claim_id == claimed.claim_id,
            # A manual resend may target a succeeded or given-up delivery too.
            true() if manual else WebhookDelivery.status == "pending",
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
            manual=manual,
        )
    )
    if result.ok:
        delivery.status = "succeeded"
    elif not manual:
        delivery.attempts += 1
        if now - delivery.created_at >= GIVE_UP_AFTER:
            delivery.status = "failed"
        else:
            step = BACKOFF[min(delivery.attempts - 1, len(BACKOFF) - 1)]
            delivery.next_attempt_at = now + timedelta(seconds=step)
    delivery.locked_until = None
    delivery.claim_id = None  # this claim is spent: a repeated finish matches nothing
    db.commit()
    return True


def deliver_one(session_factory=SessionLocal) -> bool:
    """Claim, sign, send, finish one delivery. False when nothing is due."""
    with session_factory() as db:
        claimed = claim(db)
        if claimed is None:
            return False
        result = send(claimed.url, claimed.payload, signed_headers(claimed))
        if not finish(db, claimed, result):
            log.warning("stale webhook claim delivery=%s", claimed.delivery_id)
        return True
