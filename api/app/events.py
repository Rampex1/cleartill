"""Stripe-style events: an immutable snapshot written in the same transaction as the change
(the transactional outbox, design §5.9). M13 adds a webhook delivery row per endpoint.

ponytail: `seq` follows insert order, which equals commit order only within one transaction
(or for one locked row). Polling consumers should overlap their windows; a commit-ordered
sequence isn't worth it at demo scale."""

from fastapi import Request
from sqlalchemy.orm import Session

from app import clock
from app.ids import new_id
from app.models import Event

API_VERSION = "2026-11-19"


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
    return event


def event_json(event: Event, pending_webhooks: int = 0) -> dict:
    """`pending_webhooks` is passed in (M13 counts undelivered rows with one grouped query)."""
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
