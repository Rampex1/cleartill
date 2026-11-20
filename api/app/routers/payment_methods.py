from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from app import cards, forms
from app.auth import AuthContext, require_publishable_key, require_secret_key
from app.db import get_db
from app.executor import execute
from app.ids import new_id
from app.models import PaymentMethod
from app.tenancy import get_owned

router = APIRouter(prefix="/v1/payment_methods")

Db = Annotated[Session, Depends(get_db)]
Params = Annotated[dict, Depends(forms.params)]


class CardParams(BaseModel):
    """Strings on purpose: card-specific checks (402 card_error) happen in app.cards."""

    model_config = ConfigDict(extra="forbid")

    number: str
    exp_month: str
    exp_year: str
    cvc: str


class CreatePaymentMethodParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["card"]
    card: CardParams


def payment_method_json(pm: PaymentMethod) -> dict:
    return {
        "id": pm.id,
        "object": "payment_method",
        "type": "card",
        "card": {
            "brand": pm.brand,
            "last4": pm.last4,
            "exp_month": pm.exp_month,
            "exp_year": pm.exp_year,
            "fingerprint": pm.fingerprint,
        },
        "created": int(pm.created.timestamp()),
        "livemode": False,
    }


@router.post("")
def create_payment_method(
    request: Request,
    auth: Annotated[AuthContext, Depends(require_publishable_key)],
    db: Db,
    params: Params,
):
    """Publishable key only: card numbers are never sent from a merchant's server."""

    def handler():
        data = forms.validate(CreatePaymentMethodParams, params)
        card = cards.tokenize(
            data.card.number, data.card.exp_month, data.card.exp_year, data.card.cvc
        )
        pm = PaymentMethod(
            id=new_id("pm_"),
            merchant_id=auth.merchant.id,
            brand=card.brand,
            last4=card.last4,
            exp_month=card.exp_month,
            exp_year=card.exp_year,
            fingerprint=card.fingerprint,
            sim_outcome=card.sim_outcome,
        )
        db.add(pm)
        db.flush()
        return payment_method_json(pm)

    return execute(
        db,
        request,
        auth.merchant.id,
        params,
        handler,
        hash_params=_redacted(params),
        key_scope="pk:",
    )


def _redacted(params: dict) -> dict:
    """What the idempotency hash sees: no CVC, and the number only as its fingerprint."""
    card = params.get("card")
    if not isinstance(card, dict):
        return params
    safe = {k: v for k, v in card.items() if k not in ("number", "cvc")}
    number = card.get("number")
    if isinstance(number, str):
        safe["number_fingerprint"] = cards.fingerprint(number)
    return {**params, "card": safe}


@router.get("/{pm_id}")
def retrieve_payment_method(
    pm_id: str,
    auth: Annotated[AuthContext, Depends(require_secret_key)],
    db: Db,
    params: Params,
):
    forms.validate(forms.NoParams, params)
    return payment_method_json(get_owned(db, PaymentMethod, pm_id, auth.merchant.id))
