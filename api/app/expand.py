"""One-level `expand[]` (design §5.3). Expansions load through get_owned with the caller's
merchant, so an id can never expand into another tenant's object."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import cards
from app.charges import charge_json
from app.errors import ApiError
from app.models import Charge, Customer, PaymentIntent, PaymentMethod, Refund
from app.tenancy import get_owned

PAYMENT_INTENT_PATHS = {"customer", "payment_method", "latest_charge"}
CHARGE_PATHS = {"refunds"}
MAX_PATH_ECHO = 40


def parse(paths: list[str], allowed: set[str], *, on_list: bool) -> set[str]:
    result = set()
    for path in paths:
        name = path
        if on_list:
            name = path.removeprefix("data.") if path.startswith("data.") else ""
        if name not in allowed:
            shown = f" ({path})" if len(path) <= MAX_PATH_ECHO else ""
            hint = " On lists, prefix paths with data." if on_list else ""
            raise ApiError(
                400,
                "invalid_request_error",
                f"This property cannot be expanded{shown}.{hint}",
                code="parameter_invalid",
                param="expand",
            )
        result.add(name)
    return result


def expand_payment_intent(db: Session, pi: PaymentIntent, body: dict, paths: set[str]) -> dict:
    from app.routers.customers import customer_json, deleted_json

    if "customer" in paths and pi.customer_id:
        customer = get_owned(db, Customer, pi.customer_id, pi.merchant_id, include_deleted=True)
        body["customer"] = deleted_json(customer) if customer.deleted else customer_json(customer)
    if "payment_method" in paths and pi.payment_method_id:
        pm = get_owned(db, PaymentMethod, pi.payment_method_id, pi.merchant_id)
        body["payment_method"] = cards.payment_method_json(pm)
    if "latest_charge" in paths and pi.latest_charge_id:
        charge = get_owned(db, Charge, pi.latest_charge_id, pi.merchant_id)
        body["latest_charge"] = charge_json(db, charge)
    return body


def expand_charge(db: Session, charge: Charge, body: dict, paths: set[str]) -> dict:
    from app.refunds import refund_json

    if "refunds" in paths:
        refunds = db.scalars(
            select(Refund)
            .where(Refund.charge_id == charge.id, Refund.merchant_id == charge.merchant_id)
            .order_by(Refund.seq.desc())
        )
        body["refunds"] = {
            "object": "list",
            "data": [refund_json(r) for r in refunds],
            "has_more": False,
            "url": f"/v1/charges/{charge.id}/refunds",
        }
    return body
