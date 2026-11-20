"""Public endpoints for the hosted pages (design §5.3, §5.10). No API key: the checkout
session id, or the PI's client_secret, is the credential."""

import hmac
from typing import Annotated, Literal
from urllib.parse import urlencode, urlsplit, urlunsplit

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import cards, checkout, forms, network
from app.db import get_db
from app.errors import ApiError
from app.executor import Result, execute
from app.models import CheckoutSession, PaymentIntent, PaymentMethod
from app.payment_intents import approve, confirm_pi, decline, require_status
from app.routers.payment_methods import CardParams

router = APIRouter(prefix="/checkout-api")

Db = Annotated[Session, Depends(get_db)]
Params = Annotated[dict, Depends(forms.params)]

AUTHENTICATION_FAILED = (
    "The provided PaymentMethod has failed authentication. You can provide payment_method_data "
    "or a new PaymentMethod to attempt to fulfill this PaymentIntent again."
)


class ThreeDSParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_secret: str
    result: Literal["approve", "fail"]


def _not_found() -> ApiError:
    # One error for "no such PI" and "wrong secret", so neither can be probed.
    return ApiError(
        404,
        "invalid_request_error",
        "No such payment_intent.",
        code="resource_missing",
        param="payment_intent",
    )


def _with_payment_intent(return_url: str, pi_id: str) -> str:
    """Append payment_intent to return_url, leaving the merchant's own query bytes untouched."""
    parts = urlsplit(return_url)
    added = urlencode({"payment_intent": pi_id})
    query = f"{parts.query}&{added}" if parts.query else added
    return urlunsplit(parts._replace(query=query))


@router.post("/3ds/{pi_id}")
def three_ds(pi_id: str, request: Request, db: Db, params: Params):
    def handler():
        data = forms.validate(ThreeDSParams, params)
        # Check the secret before taking any lock, so knowing a pi_id alone can't make the row
        # contend (client_secret never changes, so an unlocked read is enough).
        pi = db.get(PaymentIntent, pi_id)
        if pi is None or not hmac.compare_digest(
            pi.client_secret.encode(), data.client_secret.encode()
        ):
            raise _not_found()
        if pi.checkout_session_id is not None:  # lock order (§5.2): session before PI
            checkout.lock_session(db, pi.checkout_session_id)
        pi = db.scalar(
            select(PaymentIntent)
            .where(PaymentIntent.id == pi_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        require_status(pi, "three_ds")
        pm = db.get(PaymentMethod, pi.payment_method_id)
        # ponytail: a challenge isn't bound to one confirm attempt (Stripe's is); an old
        # challenge tab can complete a re-confirmed PI. Add a per-attempt nonce if that matters.
        outcome = (
            network.authorize(pm.sim_outcome, pi.amount, three_ds_passed=True)
            if data.result == "approve"
            else network.RequiresAuth()  # the cardholder failed the challenge
        )
        if isinstance(outcome, network.Approved):
            approve(db, pi, pm, request)
            checkout.complete_if_paid(db, pi, request)
        elif isinstance(outcome, network.Declined):
            decline(
                db, pi, pm, request, charge_code=outcome.code, decline_code=outcome.decline_code
            )
        else:
            decline(
                db,
                pi,
                pm,
                request,
                charge_code="authentication_required",
                error_code="payment_intent_authentication_failure",
                error_type="invalid_request_error",
                message=AUTHENTICATION_FAILED,
            )
        return {"redirect": _with_payment_intent(pi.return_url, pi.id)}

    return execute(db, request, None, params, handler)


class PayParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    card: CardParams


def _session_not_found() -> ApiError:
    return ApiError(
        404,
        "invalid_request_error",
        "No such checkout session.",
        code="resource_missing",
        param="session",
    )


@router.get("/sessions/{cs_id}")
def view_session(cs_id: str, db: Db):
    cs = db.get(CheckoutSession, cs_id)
    if cs is None:
        raise _session_not_found()
    return checkout.public_json(db, cs)


@router.post("/sessions/{cs_id}/pay")
def pay_session(cs_id: str, request: Request, db: Db, params: Params):
    """Card data lives only in memory here: never hashed, stored, logged or echoed.
    ponytail: no per-IP rate limit (simulated network, nothing to card-test)."""

    def handler():
        data = forms.validate(PayParams, params)
        cs = checkout.lock_session(db, cs_id)
        if cs is None:
            raise _session_not_found()
        if not checkout.is_open(cs):
            raise ApiError(
                400,
                "invalid_request_error",
                "This checkout session is no longer accepting payments.",
                code="checkout_session_unexpected_state",
            )
        pi = db.scalar(
            select(PaymentIntent)
            .where(PaymentIntent.id == cs.payment_intent_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        card = cards.tokenize(
            data.card.number, data.card.exp_month, data.card.exp_year, data.card.cvc
        )
        pm = cards.save(db, cs.merchant_id, card)
        out = confirm_pi(db, pi, payment_method=pm, request=request)
        if isinstance(out, Result):  # declined: the error only, never the PI or its secret
            error = out.body["error"]
            keys = ("type", "code", "decline_code", "message")
            return Result(out.status, {"error": {k: error[k] for k in keys if k in error}})
        if pi.status == "requires_action":
            return {"redirect": pi.next_action["redirect_to_url"]["url"]}
        checkout.complete_if_paid(db, pi, request)
        return {"redirect": checkout.success_redirect(cs)}

    return execute(db, request, None, params, handler, hash_params={})
