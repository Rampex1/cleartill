from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import forms
from app.auth import AuthContext, require_secret_key
from app.db import get_db
from app.events import emit
from app.executor import execute
from app.models import Customer, PaymentIntent
from app.payment_intents import (
    capture_pi,
    confirm_pi,
    create_pi,
    pi_json,
    require_status,
    resolve_payment_method,
)
from app.tenancy import get_owned, paginate
from app.urls import validate_redirect_url

router = APIRouter(prefix="/v1/payment_intents")

Auth = Annotated[AuthContext, Depends(require_secret_key)]
Db = Annotated[Session, Depends(get_db)]
Params = Annotated[dict, Depends(forms.params)]

MIN_PI_AMOUNT = 50  # Stripe's usd minimum


class CreateParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    amount: forms.FormInt
    currency: Literal["usd"]
    customer: str | None = None
    payment_method: str | None = None
    capture_method: Literal["automatic", "automatic_async", "manual"] = "automatic_async"
    return_url: str | None = None
    metadata: forms.Metadata | None = None
    confirm: bool = False


class UpdateParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    amount: forms.FormInt | None = None
    customer: str | None = None
    payment_method: str | None = None
    metadata: forms.Metadata | None = None


class CancelParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cancellation_reason: (
        Literal["duplicate", "fraudulent", "requested_by_customer", "abandoned"] | None
    ) = None


class ConfirmParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    payment_method: str | None = None
    return_url: str | None = None


class CaptureParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    amount_to_capture: forms.FormInt | None = None


class ListParams(forms.ListParams):
    customer: str | None = None


def _customer_id(db: Session, merchant_id: str, value: str | None) -> str | None:
    if not value:
        return None
    return get_owned(db, Customer, value, merchant_id, param="customer").id


def _payment_method_id(db: Session, merchant_id: str, value: str | None) -> str | None:
    pm = resolve_payment_method(db, merchant_id, value)
    return pm.id if pm else None


@router.post("")
def create_payment_intent(request: Request, auth: Auth, db: Db, params: Params):
    def handler():
        params_, _ = forms.pop_metadata_clear(params)
        data = forms.validate(CreateParams, params_)
        merchant_id = auth.merchant.id
        forms.require_amount(data.amount, minimum=MIN_PI_AMOUNT)
        return_url = (
            validate_redirect_url(data.return_url, "return_url") if data.return_url else None
        )
        customer_id = _customer_id(db, merchant_id, data.customer)
        pm_id = _payment_method_id(db, merchant_id, data.payment_method)
        pi = create_pi(
            db,
            merchant_id,
            amount=data.amount,
            currency=data.currency,
            capture_method=data.capture_method,
            customer_id=customer_id,
            payment_method_id=pm_id,
            return_url=return_url,
            metadata=forms.apply_metadata({}, data.metadata, clear=False),
            request=request,
        )
        if data.confirm:
            return confirm_pi(db, pi, request=request)
        return pi_json(pi)

    return execute(db, request, auth.merchant.id, params, handler)


@router.get("")
def list_payment_intents(auth: Auth, db: Db, params: Params):
    lp = forms.validate(ListParams, params)
    stmt = select(PaymentIntent)
    if lp.customer:
        stmt = stmt.where(PaymentIntent.customer_id == lp.customer)
    page = paginate(db, stmt, PaymentIntent, auth.merchant.id, lp, "/v1/payment_intents")
    page["data"] = [pi_json(pi) for pi in page["data"]]
    return page


@router.get("/{pi_id}")
def retrieve_payment_intent(pi_id: str, auth: Auth, db: Db, params: Params):
    forms.validate(forms.NoParams, params)  # M12 adds expand[]
    return pi_json(get_owned(db, PaymentIntent, pi_id, auth.merchant.id))


@router.post("/{pi_id}")
def update_payment_intent(pi_id: str, request: Request, auth: Auth, db: Db, params: Params):
    def handler():
        params_, clear_metadata = forms.pop_metadata_clear(params)
        data = forms.validate(UpdateParams, params_)
        merchant_id = auth.merchant.id
        pi = get_owned(db, PaymentIntent, pi_id, merchant_id, for_update=True)
        require_status(pi, "update")
        pi.last_payment_error = None  # Stripe clears it on any update
        sent = data.model_fields_set
        if "amount" in sent:
            pi.amount = forms.require_amount(data.amount, minimum=MIN_PI_AMOUNT)
        if "customer" in sent:
            pi.customer_id = _customer_id(db, merchant_id, data.customer)
        if "payment_method" in sent:
            pi.payment_method_id = _payment_method_id(db, merchant_id, data.payment_method)
            pi.status = (
                "requires_confirmation" if pi.payment_method_id else "requires_payment_method"
            )
        if clear_metadata or data.metadata is not None:
            pi.metadata_ = forms.apply_metadata(pi.metadata_, data.metadata, clear_metadata)
        db.flush()
        return pi_json(pi)

    return execute(db, request, auth.merchant.id, params, handler)


@router.post("/{pi_id}/cancel")
def cancel_payment_intent(pi_id: str, request: Request, auth: Auth, db: Db, params: Params):
    def handler():
        params_ = {k: v for k, v in params.items() if not (k == "cancellation_reason" and v == "")}
        data = forms.validate(CancelParams, params_)
        pi = get_owned(db, PaymentIntent, pi_id, auth.merchant.id, for_update=True)
        require_status(pi, "cancel")
        pi.status = "canceled"
        pi.cancellation_reason = data.cancellation_reason
        pi.amount_capturable = 0
        db.flush()
        body = pi_json(pi)
        emit(db, auth.merchant.id, "payment_intent.canceled", body, request)
        return body

    return execute(db, request, auth.merchant.id, params, handler)


@router.post("/{pi_id}/confirm")
def confirm_payment_intent(pi_id: str, request: Request, auth: Auth, db: Db, params: Params):
    def handler():
        data = forms.validate(ConfirmParams, params)
        merchant_id = auth.merchant.id
        pi = get_owned(db, PaymentIntent, pi_id, merchant_id, for_update=True)
        return_url = (
            validate_redirect_url(data.return_url, "return_url") if data.return_url else None
        )
        pm = resolve_payment_method(db, merchant_id, data.payment_method)
        return confirm_pi(db, pi, payment_method=pm, return_url=return_url, request=request)

    return execute(db, request, auth.merchant.id, params, handler)


@router.post("/{pi_id}/capture")
def capture_payment_intent(pi_id: str, request: Request, auth: Auth, db: Db, params: Params):
    def handler():
        data = forms.validate(CaptureParams, params)
        pi = get_owned(db, PaymentIntent, pi_id, auth.merchant.id, for_update=True)
        require_status(pi, "capture")
        amount = data.amount_to_capture
        if amount is None:
            amount = pi.amount_capturable
        forms.require_amount(
            amount, minimum=1, maximum=pi.amount_capturable, param="amount_to_capture"
        )
        return capture_pi(db, pi, amount, request)

    return execute(db, request, auth.merchant.id, params, handler)
