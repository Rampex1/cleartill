from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import forms
from app.auth import AuthContext, require_secret_key
from app.db import get_db
from app.executor import execute
from app.models import Payout
from app.payouts import create_payout, payout_json
from app.tenancy import get_owned, paginate

router = APIRouter(prefix="/v1/payouts")

Auth = Annotated[AuthContext, Depends(require_secret_key)]
Db = Annotated[Session, Depends(get_db)]
Params = Annotated[dict, Depends(forms.params)]


class CreateParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    amount: forms.FormInt
    currency: Literal["usd"]


@router.post("")
def create(request: Request, auth: Auth, db: Db, params: Params):
    def handler():
        data = forms.validate(CreateParams, params)
        forms.require_amount(data.amount, minimum=1)
        payout = create_payout(db, auth.merchant.id, data.amount, data.currency, request)
        return payout_json(payout)

    return execute(db, request, auth.merchant.id, params, handler)


@router.get("")
def list_payouts(auth: Auth, db: Db, params: Params):
    lp = forms.validate(forms.ListParams, params)
    page = paginate(db, select(Payout), Payout, auth.merchant.id, lp, "/v1/payouts")
    page["data"] = [payout_json(p) for p in page["data"]]
    return page


@router.get("/{payout_id}")
def retrieve(payout_id: str, auth: Auth, db: Db, params: Params):
    forms.validate(forms.NoParams, params)
    return payout_json(get_owned(db, Payout, payout_id, auth.merchant.id))
