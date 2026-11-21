"""Stripe-style events: an immutable snapshot written in the same transaction as the change
(the transactional outbox, design §5.9). M13 adds a webhook delivery row per endpoint.

ponytail: `seq` follows insert order, which equals commit order only within one transaction
(or for one locked row). Polling consumers should overlap their windows; a commit-ordered
sequence isn't worth it at demo scale."""

from fastapi import Request
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app import clock
from app.ids import new_id
from app.models import Event, WebhookDelivery, WebhookEndpoint

API_VERSION = "2026-11-19"

KNOWN_EVENT_TYPES = frozenset(
    {
        "customer.created",
        "customer.updated",
        "customer.deleted",
        "payment_intent.created",
        "payment_intent.succeeded",
        "payment_intent.payment_failed",
        "payment_intent.requires_action",
        "payment_intent.amount_capturable_updated",
        "payment_intent.canceled",
        "charge.succeeded",
        "charge.failed",
        "charge.captured",
        "charge.refunded",
        "refund.created",
        "checkout.session.completed",
        "checkout.session.expired",
        "payout.created",
        "payout.paid",
    }
)


def emit(
    db: Session,
    merchant_id: str,
    event_type: str,
    obj: dict,
    request: Request | None = None,
) -> Event:
    """Record an event. `request` is None for automatic events (the worker's expiries)."""
    state = request.state if request is not None else None
    event = Event(
        id=new_id("evt_"),
        merchant_id=merchant_id,
        type=event_type,
        data={"object": obj},
        request={
            "id": getattr(state, "request_id", None),
            # The key the executor actually applied (POST only), not whatever header was sent.
            "idempotency_key": getattr(state, "idempotency_key", None),
        },
        created=clock.now(),
    )
    db.add(event)
    db.flush()  # each emit gets the next seq, so events keep their emission order
    # The outbox: one pending delivery per matching endpoint, in this same transaction.
    endpoint_ids = db.scalars(
        select(WebhookEndpoint.id)
        .where(
            WebhookEndpoint.merchant_id == merchant_id,
            WebhookEndpoint.status == "enabled",
            WebhookEndpoint.deleted.is_(False),
            or_(
                WebhookEndpoint.enabled_events.contains([event_type]),
                WebhookEndpoint.enabled_events.contains(["*"]),
            ),
        )
        # FOR SHARE: a concurrent disable/delete either waits for this transaction (and then
        # fails the new row) or commits first (and this select then skips the endpoint).
        .with_for_update(read=True)
    ).all()
    now = clock.now()
    for endpoint_id in endpoint_ids:
        db.add(
            WebhookDelivery(
                id=new_id("wd_"),
                merchant_id=merchant_id,
                event_id=event.id,
                endpoint_id=endpoint_id,
                status="pending",
                attempts=0,
                next_attempt_at=now,
                created_at=now,
            )
        )
    if endpoint_ids:
        db.flush()
    return event


def pending_counts(db: Session, event_ids: list[str]) -> dict[str, int]:
    """Undelivered webhook count per event, in one query (`pending_webhooks`)."""
    if not event_ids:
        return {}
    rows = db.execute(
        select(WebhookDelivery.event_id, func.count())
        .where(WebhookDelivery.event_id.in_(event_ids), WebhookDelivery.status == "pending")
        .group_by(WebhookDelivery.event_id)
    ).all()
    return dict(rows)


def event_json(event: Event, pending_webhooks: int = 0) -> dict:
    """`pending_webhooks` is passed in (see `pending_counts`)."""
    return {
        "id": event.id,
        "object": "event",
        "api_version": API_VERSION,
        "created": int(event.created.timestamp()),
        "data": event.data,
        "livemode": False,
        "pending_webhooks": pending_webhooks,
        "request": event.request,
        "type": event.type,
    }
