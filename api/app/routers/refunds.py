from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import forms
from app.auth import AuthContext, require_secret_key
from app.db import get_db
from app.executor import execute
from app.models import Refund
from app.refunds import create_refund, refund_json
from app.tenancy import get_owned, paginate

router = APIRouter(prefix="/v1/refunds")

Auth = Annotated[AuthContext, Depends(require_secret_key)]
Db = Annotated[Session, Depends(get_db)]
Params = Annotated[dict, Depends(forms.params)]


class CreateRefundParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    charge: str | None = None
    payment_intent: str | None = None
    amount: forms.FormInt | None = None
    reason: Literal["duplicate", "fraudulent", "requested_by_customer"] | None = None


class ListRefundsParams(forms.ListParams):
    charge: str | None = None
    payment_intent: str | None = None


@router.post("")
def create(request: Request, auth: Auth, db: Db, params: Params):
    def handler():
        cleaned = {k: v for k, v in params.items() if not (k == "reason" and v == "")}
        data = forms.validate(CreateRefundParams, cleaned)
        return create_refund(
            db,
            auth.merchant.id,
            charge_id=data.charge,
            payment_intent_id=data.payment_intent,
            amount=data.amount,
            reason=data.reason,
            request=request,
        )

    return execute(db, request, auth.merchant.id, params, handler)


@router.get("")
def list_refunds(auth: Auth, db: Db, params: Params):
    lp = forms.validate(ListRefundsParams, params)
    stmt = select(Refund)
    if lp.charge:
        stmt = stmt.where(Refund.charge_id == lp.charge)
    if lp.payment_intent:
        stmt = stmt.where(Refund.payment_intent_id == lp.payment_intent)
    page = paginate(db, stmt, Refund, auth.merchant.id, lp, "/v1/refunds")
    page["data"] = [refund_json(r) for r in page["data"]]
    return page


@router.get("/{refund_id}")
def retrieve(refund_id: str, auth: Auth, db: Db, params: Params):
    forms.validate(forms.NoParams, params)
    return refund_json(get_owned(db, Refund, refund_id, auth.merchant.id))
