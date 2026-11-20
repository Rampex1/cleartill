from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import forms
from app.auth import AuthContext, require_secret_key
from app.charges import charge_json
from app.db import get_db
from app.models import Charge
from app.tenancy import get_owned, paginate

router = APIRouter(prefix="/v1/charges")

Auth = Annotated[AuthContext, Depends(require_secret_key)]
Db = Annotated[Session, Depends(get_db)]
Params = Annotated[dict, Depends(forms.params)]


class ListChargesParams(forms.ListParams):
    payment_intent: str | None = None


@router.get("")
def list_charges(auth: Auth, db: Db, params: Params):
    lp = forms.validate(ListChargesParams, params)
    stmt = select(Charge)
    if lp.payment_intent:
        stmt = stmt.where(Charge.payment_intent_id == lp.payment_intent)
    page = paginate(db, stmt, Charge, auth.merchant.id, lp, "/v1/charges")
    page["data"] = [charge_json(db, c) for c in page["data"]]
    return page


@router.get("/{charge_id}")
def retrieve_charge(charge_id: str, auth: Auth, db: Db, params: Params):
    forms.validate(forms.NoParams, params)
    return charge_json(db, get_owned(db, Charge, charge_id, auth.merchant.id))
