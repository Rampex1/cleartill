from typing import Annotated, Literal

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import forms, ledger
from app.auth import AuthContext, require_secret_key
from app.db import get_db
from app.models import BalanceTransaction
from app.tenancy import get_owned, paginate

router = APIRouter(prefix="/v1")

Auth = Annotated[AuthContext, Depends(require_secret_key)]
Db = Annotated[Session, Depends(get_db)]
Params = Annotated[dict, Depends(forms.params)]


class ListBalanceTransactionsParams(forms.ListParams):
    type: Literal["charge", "refund", "payout"] | None = None


def balance_transaction_json(bt: BalanceTransaction) -> dict:
    return {
        "id": bt.id,
        "object": "balance_transaction",
        "amount": bt.amount,
        "fee": bt.fee,
        "net": bt.net,
        "currency": bt.currency,
        "type": bt.type,
        "status": bt.status,
        "available_on": int(bt.available_on.timestamp()),
        "source": bt.source_id,
        "created": int(bt.created.timestamp()),
        "livemode": False,
    }


@router.get("/balance")
def get_balance(auth: Auth, db: Db, params: Params):
    forms.validate(forms.NoParams, params)
    return {"object": "balance", **ledger.balance(db, auth.merchant.id), "livemode": False}


@router.get("/balance_transactions")
def list_balance_transactions(auth: Auth, db: Db, params: Params):
    lp = forms.validate(ListBalanceTransactionsParams, params)
    stmt = select(BalanceTransaction)
    if lp.type:
        stmt = stmt.where(BalanceTransaction.type == lp.type)
    page = paginate(db, stmt, BalanceTransaction, auth.merchant.id, lp, "/v1/balance_transactions")
    page["data"] = [balance_transaction_json(bt) for bt in page["data"]]
    return page


@router.get("/balance_transactions/{bt_id}")
def retrieve_balance_transaction(bt_id: str, auth: Auth, db: Db, params: Params):
    forms.validate(forms.NoParams, params)
    return balance_transaction_json(get_owned(db, BalanceTransaction, bt_id, auth.merchant.id))
