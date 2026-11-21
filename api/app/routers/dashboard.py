"""Dashboard-only endpoints: signup, login, logout, session, API keys."""

import threading
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, BeforeValidator, ConfigDict, StringConstraints
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import clock, delivery, demo, forms, ratelimit, timeline
from app.auth import AuthContext, require_dashboard_header, require_session
from app.db import get_db
from app.errors import ApiError
from app.executor import execute
from app.merchants import create_merchant, new_api_key, normalize_email
from app.models import (
    ApiKey,
    Event,
    Merchant,
    PaymentIntent,
    WebhookAttempt,
    WebhookDelivery,
    WebhookEndpoint,
)
from app.passwords import DUMMY_HASH, hash_password, needs_rehash, verify_password
from app.sessions import (
    COOKIE_NAME,
    clear_session_cookie,
    create_session,
    delete_session,
    set_session_cookie,
)
from app.tenancy import get_owned

router = APIRouter(prefix="/dashboard-api")

Db = Annotated[Session, Depends(get_db)]
Params = Annotated[dict, Depends(forms.params)]
SessionAuth = Annotated[AuthContext, Depends(require_session)]
DashboardHeader = [Depends(require_dashboard_header)]

_normalize = BeforeValidator(lambda v: normalize_email(v) if isinstance(v, str) else v)
Email = Annotated[str, _normalize, StringConstraints(max_length=254, pattern=r"^[^@\s]+@[^@\s]+$")]


class SignupParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: Email
    password: Annotated[str, StringConstraints(min_length=8, max_length=128)]


class LoginParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: Annotated[str, _normalize, StringConstraints(max_length=254)]
    password: Annotated[str, StringConstraints(max_length=128)]


def _account_exists() -> ApiError:
    return ApiError(
        400,
        "invalid_request_error",
        "An account with this email already exists.",
        code="account_exists",
        param="email",
    )


def merchant_json(m: Merchant) -> dict:
    return {"id": m.id, "email": m.email}


def key_json(k: ApiKey) -> dict:
    return {
        "id": k.id,
        "object": "api_key",
        "kind": k.kind,
        "last4": k.last4,
        "created": int(k.created.timestamp()),
    }


@router.post("/signup", dependencies=DashboardHeader)
def signup(request: Request, db: Db, params: Params):
    ratelimit.check("signup", ratelimit.client_ip(request))
    session = {}

    def handler():
        data = forms.validate(SignupParams, params)
        if db.scalar(select(Merchant.id).where(Merchant.email == data.email)):
            raise _account_exists()
        try:
            merchant, secret_key, publishable_key = create_merchant(
                db, data.email, password=data.password
            )
        except IntegrityError:
            raise _account_exists() from None  # a concurrent signup won the race
        session["token"] = create_session(db, merchant.id)
        return {
            "merchant": merchant_json(merchant),
            "secret_key": secret_key,
            "publishable_key": publishable_key,
        }

    response = execute(db, request, None, params, handler)
    set_session_cookie(response, session["token"])
    return response


@router.post("/login", dependencies=DashboardHeader)
def login(request: Request, db: Db, params: Params):
    ratelimit.check("login", ratelimit.client_ip(request))
    session = {}

    def handler():
        data = forms.validate(LoginParams, params)
        merchant = db.scalar(select(Merchant).where(Merchant.email == data.email))
        stored = merchant.password_hash if merchant and merchant.password_hash else None
        # Always run scrypt, so timing doesn't reveal whether the account exists.
        valid = verify_password(data.password, stored or DUMMY_HASH) and stored is not None
        if not valid:
            raise ApiError(
                401,
                "invalid_request_error",
                "Invalid email or password.",
                code="invalid_credentials",
            )
        if needs_rehash(stored):
            merchant.password_hash = hash_password(data.password)
        old_token = request.cookies.get(COOKIE_NAME)
        if old_token:
            delete_session(db, old_token)  # replace, don't accumulate, the presented session
        session["token"] = create_session(db, merchant.id)  # always new: no fixation
        return {"merchant": merchant_json(merchant)}

    response = execute(db, request, None, params, handler)
    set_session_cookie(response, session["token"])
    return response


@router.post("/logout", dependencies=DashboardHeader)
def logout(request: Request, db: Db, params: Params):
    """Idempotent: always clears the cookie, even if the session already expired."""

    def handler():
        forms.validate(forms.NoParams, params)
        token = request.cookies.get(COOKIE_NAME)
        if token:
            delete_session(db, token)
        return {"logged_out": True}

    # merchant_id=None: no Idempotency-Key storage (nothing to replay; keys are per merchant).
    response = execute(db, request, None, params, handler)
    clear_session_cookie(response)
    return response


@router.get("/session")
def current_session(auth: SessionAuth):
    return {"merchant": merchant_json(auth.merchant)}


@router.get("/keys")
def list_keys(auth: SessionAuth, db: Db):
    keys = db.scalars(
        select(ApiKey)
        .where(ApiKey.merchant_id == auth.merchant.id, ApiKey.revoked_at.is_(None))
        .order_by(ApiKey.seq.desc())
    )
    return {"object": "list", "data": [key_json(k) for k in keys]}


@router.post("/keys/{key_id}/roll")
def roll_key(key_id: str, request: Request, auth: SessionAuth, db: Db, params: Params):
    def handler():
        forms.validate(forms.NoParams, params)
        old = db.scalar(
            select(ApiKey)
            .where(
                ApiKey.id == key_id,
                ApiKey.merchant_id == auth.merchant.id,
                ApiKey.revoked_at.is_(None),
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if old is None:
            raise ApiError(
                404, "invalid_request_error", "No such API key.", code="resource_missing"
            )
        old.revoked_at = clock.now()  # revoked immediately
        new, secret = new_api_key(db, auth.merchant.id, old.kind)
        db.flush()
        return {**key_json(new), "secret": secret}

    # merchant_id=None: never store this response for idempotent replay — it holds a live
    # secret key. A double-submitted roll is safe anyway: the old key is already revoked (404).
    return execute(db, request, None, params, handler)


# --- webhooks (M20): delivery log and synchronous resend ------------------------------------

DELIVERY_PAGE = 100  # ponytail: newest 100 per endpoint; paginate if anyone needs more


def _unix(value) -> int | None:
    return int(value.timestamp()) if value is not None else None


def delivery_json(d: WebhookDelivery, event_type: str) -> dict:
    return {
        "id": d.id,
        "object": "webhook_delivery",
        "event": d.event_id,
        "type": event_type,
        "status": d.status,
        "attempts": d.attempts,
        "next_attempt_at": _unix(d.next_attempt_at),
        "created": _unix(d.created_at),
    }


def attempt_json(a: WebhookAttempt) -> dict:
    return {
        "id": a.id,
        "object": "webhook_attempt",
        "at": _unix(a.at),
        "status_code": a.status_code,
        "error": a.error,
        "duration_ms": a.duration_ms,
        "manual": a.manual,
    }


def _event_type(db: Session, event_id: str) -> str:
    return db.scalar(select(Event.type).where(Event.id == event_id))


@router.get("/webhook_endpoints/{endpoint_id}/deliveries")
def list_deliveries(endpoint_id: str, auth: SessionAuth, db: Db):
    endpoint = get_owned(db, WebhookEndpoint, endpoint_id, auth.merchant.id, include_deleted=True)
    rows = db.execute(
        select(WebhookDelivery, Event.type)
        .join(Event, Event.id == WebhookDelivery.event_id)
        .where(
            WebhookDelivery.endpoint_id == endpoint.id,
            WebhookDelivery.merchant_id == auth.merchant.id,
        )
        .order_by(WebhookDelivery.created_at.desc(), WebhookDelivery.id.desc())
        .limit(DELIVERY_PAGE)
    ).all()
    return {"object": "list", "data": [delivery_json(d, t) for d, t in rows]}


@router.get("/webhook_deliveries/{delivery_id}/attempts")
def list_attempts(delivery_id: str, auth: SessionAuth, db: Db):
    d = get_owned(db, WebhookDelivery, delivery_id, auth.merchant.id)
    attempts = db.scalars(
        select(WebhookAttempt)
        .where(WebhookAttempt.delivery_id == d.id, WebhookAttempt.merchant_id == auth.merchant.id)
        .order_by(WebhookAttempt.at, WebhookAttempt.id)
    )
    return {"object": "list", "data": [attempt_json(a) for a in attempts]}


# ponytail: per-process cap on concurrent synchronous resends (each holds a worker thread for
# up to ~20 s against a merchant-chosen URL); move resends to the worker queue if it bites.
RESEND_SLOTS = threading.BoundedSemaphore(4)
RESENDS_PER_MINUTE = 10


def _busy() -> ApiError:
    return ApiError(
        429,
        "invalid_request_error",
        "Too many resends right now. Try again in a moment.",
        code="rate_limit",
    )


@router.post("/webhook_deliveries/{delivery_id}/resend")
def resend_delivery(delivery_id: str, auth: SessionAuth, db: Db):
    """Send now and record a manual attempt. Not run through the executor: the HTTP call
    must happen outside any transaction (claim commits, send, then a fenced finish)."""
    ratelimit.check("resend", auth.merchant.id, limit=RESENDS_PER_MINUTE)
    if not RESEND_SLOTS.acquire(blocking=False):
        raise _busy()
    try:
        claimed = delivery.claim_manual(db, auth.merchant.id, delivery_id)
        result = delivery.send(claimed.url, claimed.payload, delivery.signed_headers(claimed))
    finally:
        RESEND_SLOTS.release()
    if not delivery.finish(db, claimed, result, manual=True):
        # The claim went stale mid-send (never with today's lock vs. send deadline): the send
        # happened but wasn't recorded, so don't report some older attempt as this one.
        raise ApiError(
            409,
            "invalid_request_error",
            "The resend's result couldn't be recorded. Reload to see the delivery's state.",
            code="delivery_in_progress",
        )
    d = get_owned(db, WebhookDelivery, delivery_id, auth.merchant.id)
    attempt = db.scalars(  # ours: we held the claim until finish, so it's the newest manual one
        select(WebhookAttempt)
        .where(WebhookAttempt.delivery_id == d.id, WebhookAttempt.manual.is_(True))
        .order_by(WebhookAttempt.at.desc(), WebhookAttempt.id.desc())
        .limit(1)
    ).first()
    return {
        "delivery": delivery_json(d, _event_type(db, d.event_id)),
        "attempt": attempt_json(attempt),
    }


# --- payment timeline (dashboard payment detail) --------------------------------------------


@router.get("/payment_intents/{pi_id}/timeline")
def payment_timeline(pi_id: str, auth: SessionAuth, db: Db):
    """Every event about this payment and every journal transaction behind it, oldest first:
    the payment detail page pins each posting to the step that caused it."""
    pi = get_owned(db, PaymentIntent, pi_id, auth.merchant.id)
    return timeline.for_payment(db, auth.merchant.id, pi)


# --- demo accounts --------------------------------------------------------------------------

DEMO_ACCOUNTS_PER_MINUTE = 5


@router.post("/demo-account", dependencies=DashboardHeader)
def create_demo_account(request: Request, db: Db, params: Params):
    """A fresh sandbox merchant with two weeks of history, logged in at once (no password:
    the session is the only way in). One per click, so visitors never share state."""
    ratelimit.check("demo-account", ratelimit.client_ip(request), limit=DEMO_ACCOUNTS_PER_MINUTE)
    session = {}

    def handler():
        forms.validate(forms.NoParams, params)
        merchant = demo.create_demo_merchant(db)
        session["token"] = create_session(db, merchant.id)
        return {"merchant": merchant_json(merchant)}

    response = execute(db, request, None, params, handler)
    set_session_cookie(response, session["token"])
    return response
