from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, StringConstraints
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import forms
from app.auth import AuthContext, require_secret_key
from app.db import get_db
from app.errors import ApiError
from app.ids import new_id
from app.models import Customer
from app.tenancy import get_owned, paginate

router = APIRouter(prefix="/v1/customers")

Auth = Annotated[AuthContext, Depends(require_secret_key)]
Db = Annotated[Session, Depends(get_db)]
Params = Annotated[dict, Depends(forms.params)]

Email = Annotated[str, StringConstraints(max_length=512)]
Name = Annotated[str, StringConstraints(max_length=256)]
MAX_METADATA_KEYS = 50


class CustomerParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: Email | None = None
    name: Name | None = None
    metadata: forms.Metadata | None = None


class ListCustomersParams(forms.ListParams):
    email: Email | None = None


def customer_json(c: Customer) -> dict:
    return {
        "id": c.id,
        "object": "customer",
        "created": int(c.created.timestamp()),
        "livemode": False,
        "email": c.email,
        "name": c.name,
        "metadata": c.metadata_,
    }


def deleted_json(c: Customer) -> dict:
    return {"id": c.id, "object": "customer", "deleted": True}


def _parse(params: dict) -> tuple[CustomerParams, bool]:
    """`metadata=` (empty) means "clear all keys"; `metadata[k]=` means "remove k"."""
    params = dict(params)
    clear_metadata = params.get("metadata") == ""
    if clear_metadata:
        del params["metadata"]
    return forms.validate(CustomerParams, params), clear_metadata


@router.post("")
def create_customer(auth: Auth, db: Db, params: Params):
    data, _ = _parse(params)
    customer = Customer(
        id=new_id("cus_"),
        merchant_id=auth.merchant.id,
        email=data.email or None,
        name=data.name or None,
        metadata_={k: v for k, v in (data.metadata or {}).items() if v != ""},
    )
    db.add(customer)
    db.flush()
    body = customer_json(customer)
    db.commit()  # M4's request executor takes over transaction handling.
    return body


@router.get("")
def list_customers(auth: Auth, db: Db, params: Params):
    lp = forms.validate(ListCustomersParams, params)
    stmt = select(Customer).where(
        Customer.merchant_id == auth.merchant.id, Customer.deleted.is_(False)
    )
    if lp.email is not None:
        stmt = stmt.where(Customer.email == lp.email)
    page = paginate(db, stmt, Customer, auth.merchant.id, lp, "/v1/customers")
    page["data"] = [customer_json(c) for c in page["data"]]
    return page


@router.get("/{customer_id}")
def retrieve_customer(customer_id: str, auth: Auth, db: Db):
    customer = get_owned(db, Customer, customer_id, auth.merchant.id, include_deleted=True)
    return deleted_json(customer) if customer.deleted else customer_json(customer)


@router.post("/{customer_id}")
def update_customer(customer_id: str, auth: Auth, db: Db, params: Params):
    data, clear_metadata = _parse(params)
    customer = get_owned(db, Customer, customer_id, auth.merchant.id, for_update=True)
    sent = data.model_fields_set
    if "email" in sent:
        customer.email = data.email or None
    if "name" in sent:
        customer.name = data.name or None
    if clear_metadata or data.metadata is not None:
        merged = {} if clear_metadata else dict(customer.metadata_)
        for key, value in (data.metadata or {}).items():
            if value == "":
                merged.pop(key, None)
            else:
                merged[key] = value
        if len(merged) > MAX_METADATA_KEYS:
            raise ApiError(
                400,
                "invalid_request_error",
                f"Metadata can have at most {MAX_METADATA_KEYS} keys.",
                code="parameter_invalid",
                param="metadata",
            )
        customer.metadata_ = merged
    db.flush()
    body = customer_json(customer)
    db.commit()
    return body


@router.delete("/{customer_id}")
def delete_customer(customer_id: str, auth: Auth, db: Db):
    customer = get_owned(db, Customer, customer_id, auth.merchant.id, for_update=True)
    customer.deleted = True
    db.flush()
    body = deleted_json(customer)
    db.commit()
    return body
