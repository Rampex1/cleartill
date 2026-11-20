from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import expand, forms
from app.auth import AuthContext, require_secret_key
from app.charges import charge_json
from app.db import get_db
from app.models import Charge, Refund
from app.refunds import refund_json
from app.tenancy import get_owned, paginate

router = APIRouter(prefix="/v1/charges")

Auth = Annotated[AuthContext, Depends(require_secret_key)]
Db = Annotated[Session, Depends(get_db)]
Params = Annotated[dict, Depends(forms.params)]


class ListChargesParams(forms.ListParams):
    payment_intent: str | None = None
    expand: forms.FormList[str] = []


@router.get("")
def list_charges(auth: Auth, db: Db, params: Params):
    lp = forms.validate(ListChargesParams, params)
    stmt = select(Charge)
    if lp.payment_intent:
        stmt = stmt.where(Charge.payment_intent_id == lp.payment_intent)
    paths = expand.parse(lp.expand, expand.CHARGE_PATHS, on_list=True)
    page = paginate(db, stmt, Charge, auth.merchant.id, lp, "/v1/charges")
    refunds = expand.preload_charges(db, page["data"], paths, auth.merchant.id)
    page["data"] = [
        expand.expand_charge(db, c, charge_json(db, c), paths, refunds) for c in page["data"]
    ]
    return page


@router.get("/{charge_id}")
def retrieve_charge(charge_id: str, auth: Auth, db: Db, params: Params):
    data = forms.validate(forms.ExpandParams, params)
    paths = expand.parse(data.expand, expand.CHARGE_PATHS, on_list=False)
    charge = get_owned(db, Charge, charge_id, auth.merchant.id)
    return expand.expand_charge(db, charge, charge_json(db, charge), paths)


@router.get("/{charge_id}/refunds")
def list_charge_refunds(charge_id: str, auth: Auth, db: Db, params: Params):
    """The `url` of an expanded charge.refunds list (as on Stripe)."""
    lp = forms.validate(forms.ListParams, params)
    charge = get_owned(db, Charge, charge_id, auth.merchant.id)
    stmt = select(Refund).where(Refund.charge_id == charge.id)
    page = paginate(db, stmt, Refund, auth.merchant.id, lp, f"/v1/charges/{charge.id}/refunds")
    page["data"] = [refund_json(r) for r in page["data"]]
    return page
