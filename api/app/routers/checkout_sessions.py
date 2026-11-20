from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import checkout, clock, forms
from app.auth import AuthContext, require_secret_key
from app.db import get_db
from app.executor import execute
from app.ids import new_id
from app.models import CheckoutSession, Customer
from app.payment_intents import create_pi
from app.tenancy import get_owned, paginate
from app.urls import validate_redirect_url

router = APIRouter(prefix="/v1/checkout/sessions")

Auth = Annotated[AuthContext, Depends(require_secret_key)]
Db = Annotated[Session, Depends(get_db)]
Params = Annotated[dict, Depends(forms.params)]

MIN_TOTAL = 50


class ProductData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Annotated[str, StringConstraints(min_length=1, max_length=250)]


class PriceData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    currency: Literal["usd"]
    unit_amount: forms.FormInt
    product_data: ProductData


class LineItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    price_data: PriceData
    quantity: forms.FormInt


class CreateParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["payment"]
    line_items: Annotated[forms.FormList[LineItem], Field(min_length=1, max_length=100)]
    success_url: str
    cancel_url: str | None = None
    customer: str | None = None
    client_reference_id: Annotated[str, StringConstraints(max_length=200)] | None = None
    metadata: forms.Metadata | None = None


@router.post("")
def create_session(request: Request, auth: Auth, db: Db, params: Params):
    def handler():
        params_, _ = forms.pop_metadata_clear(params)
        data = forms.validate(CreateParams, params_)
        merchant_id = auth.merchant.id
        items = []
        for item in data.line_items:
            forms.require_amount(item.quantity, minimum=1, param="line_items")
            unit = forms.require_amount(item.price_data.unit_amount, minimum=0, param="line_items")
            items.append(
                {
                    "name": item.price_data.product_data.name,
                    "quantity": item.quantity,
                    "unit_amount": unit,
                    "amount_total": unit * item.quantity,
                }
            )
        total = sum(i["amount_total"] for i in items)
        forms.require_amount(total, minimum=MIN_TOTAL, param="line_items")
        success_url = validate_redirect_url(data.success_url, "success_url")
        cancel_url = (
            validate_redirect_url(data.cancel_url, "cancel_url") if data.cancel_url else None
        )
        customer_id = (
            get_owned(db, Customer, data.customer, merchant_id, param="customer").id
            if data.customer
            else None
        )
        cs = CheckoutSession(
            id=new_id("cs_"),
            merchant_id=merchant_id,
            amount_total=total,
            currency="usd",
            line_items=items,
            status="open",
            payment_status="unpaid",
            success_url=success_url,
            cancel_url=cancel_url,
            customer_id=customer_id,
            client_reference_id=data.client_reference_id or None,
            metadata_=forms.apply_metadata({}, data.metadata, clear=False),
            expires_at=clock.now() + checkout.SESSION_LIFETIME,
        )
        db.add(cs)
        db.flush()
        pi = create_pi(
            db,
            merchant_id,
            amount=total,
            currency="usd",
            capture_method="automatic",
            customer_id=customer_id,
            return_url=checkout.session_json(cs)["url"],
            checkout_session_id=cs.id,
            request=request,
        )
        cs.payment_intent_id = pi.id
        db.flush()
        return checkout.session_json(cs)

    return execute(db, request, auth.merchant.id, params, handler)


@router.get("")
def list_sessions(auth: Auth, db: Db, params: Params):
    lp = forms.validate(forms.ListParams, params)
    page = paginate(
        db, select(CheckoutSession), CheckoutSession, auth.merchant.id, lp, "/v1/checkout/sessions"
    )
    page["data"] = [checkout.session_json(cs) for cs in page["data"]]
    return page


@router.get("/{cs_id}")
def retrieve_session(cs_id: str, auth: Auth, db: Db, params: Params):
    forms.validate(forms.NoParams, params)
    return checkout.session_json(get_owned(db, CheckoutSession, cs_id, auth.merchant.id))
