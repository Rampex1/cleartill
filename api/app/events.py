"""Stripe-style events: an immutable snapshot written in the same transaction as the change
(the transactional outbox, design §5.9). M13 adds a webhook delivery row per endpoint."""

from fastapi import Request
from sqlalchemy.orm import Session

from app import clock
from app.ids import new_id
from app.models import Event

API_VERSION = "2026-11-19"


def emit(db: Session, request: Request, merchant_id: str, type: str, obj: dict) -> Event:
    event = Event(
        id=new_id("evt_"),
        merchant_id=merchant_id,
        type=type,
        data={"object": obj},
        request={
            "id": getattr(request.state, "request_id", None),
            "idempotency_key": request.headers.get("idempotency-key"),
        },
        created=clock.now(),
    )
    db.add(event)
    db.flush()  # keep seq in emission order
    return event


def event_json(event: Event) -> dict:
    return {
        "id": event.id,
        "object": "event",
        "api_version": API_VERSION,
        "created": int(event.created.timestamp()),
        "data": event.data,
        "livemode": False,
        "pending_webhooks": 0,  # M13 counts undelivered webhook deliveries
        "request": event.request,
        "type": event.type,
    }
