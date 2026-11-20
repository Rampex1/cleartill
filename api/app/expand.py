"""One-level `expand[]` (design §5.3).

Every expanded object is checked against the caller's merchant. On lists, `preload_*` fetch
each path's objects for the whole page in one merchant-scoped query and return them in a
cache. The caller keeps the cache alive while rendering, because the session's identity map
only holds weak references, so per-row lookups cost no query."""

import re
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import cards
from app.charges import charge_json
from app.customers import customer_json, deleted_json
from app.errors import ApiError
from app.models import Charge, Customer, PaymentIntent, PaymentMethod, Refund
from app.refunds import refund_json

PAYMENT_INTENT_PATHS = {"customer", "payment_method", "latest_charge"}
CHARGE_PATHS = {"refunds"}
# Echo a rejected path only if it looks like one: never a client value such as a card number.
_PATH_SHAPE = re.compile(r"[a-z_]{1,30}(\.[a-z_]{1,30}){0,2}")


def _bad_path(path: str, on_list: bool) -> ApiError:
    shown = f" ({path})" if _PATH_SHAPE.fullmatch(path) else ""
    hint = " On lists, prefix paths with data." if on_list and not path.startswith("data.") else ""
    message = (
        "Expand paths can't be empty."
        if not path
        else (f"This property cannot be expanded{shown}.{hint}")
    )
    return ApiError(400, "invalid_request_error", message, code="parameter_invalid", param="expand")


def parse(paths: list[str], allowed: set[str], *, on_list: bool) -> set[str]:
    result = set()
    for path in paths:
        name = path
        if on_list:
            name = path.removeprefix("data.") if path.startswith("data.") else ""
        if name not in allowed:
            raise _bad_path(path, on_list)
        result.add(name)
    return result


def _owned(db: Session, model, object_id: str, merchant_id: str):
    obj = db.get(model, object_id)  # identity map first: preloaded (cached) rows cost no query
    if obj is None or obj.merchant_id != merchant_id:
        raise ApiError(
            404,
            "invalid_request_error",
            f"No such {model.object_name} to expand.",
            code="resource_missing",
            param="expand",
        )
    return obj


def _preload(db: Session, model, ids: set[str], merchant_id: str, cache: list) -> list:
    if not ids:
        return []
    # populate_existing: refresh rows the identity map holds but an earlier commit expired.
    rows = db.scalars(
        select(model)
        .where(model.id.in_(ids), model.merchant_id == merchant_id)
        .execution_options(populate_existing=True)
    ).all()
    cache.extend(rows)  # strong references keep them in the identity map
    return rows


def preload_payment_intents(
    db: Session, pis: list[PaymentIntent], paths: set[str], merchant_id: str
) -> list:
    """Returns the cache; keep it referenced until the page is rendered."""
    cache: list = []
    if "customer" in paths:
        ids = {p.customer_id for p in pis if p.customer_id}
        _preload(db, Customer, ids, merchant_id, cache)
    if "payment_method" in paths:
        ids = {p.payment_method_id for p in pis if p.payment_method_id}
        _preload(db, PaymentMethod, ids, merchant_id, cache)
    if "latest_charge" in paths:
        ids = {p.latest_charge_id for p in pis if p.latest_charge_id}
        charges = _preload(db, Charge, ids, merchant_id, cache)
        _preload(db, PaymentMethod, {c.payment_method_id for c in charges}, merchant_id, cache)
    return cache


def preload_charges(db: Session, charges: list[Charge], paths: set[str], merchant_id: str) -> dict:
    """Loads the charges' payment methods (charge JSON needs them) and, if expanded, their
    refunds in one query. Returns refunds grouped by charge id; the payment methods are kept
    alive under the "_cache" key."""
    cache: list = []
    _preload(db, PaymentMethod, {c.payment_method_id for c in charges}, merchant_id, cache)
    grouped: dict = defaultdict(list)
    grouped["_cache"] = cache
    if "refunds" in paths and charges:
        rows = db.scalars(
            select(Refund)
            .where(Refund.charge_id.in_({c.id for c in charges}), Refund.merchant_id == merchant_id)
            .order_by(Refund.seq.desc())
        )
        for refund in rows:
            grouped[refund.charge_id].append(refund)
    return grouped


def expand_payment_intent(db: Session, pi: PaymentIntent, body: dict, paths: set[str]) -> dict:
    if "customer" in paths and pi.customer_id:
        customer = _owned(db, Customer, pi.customer_id, pi.merchant_id)
        body["customer"] = deleted_json(customer) if customer.deleted else customer_json(customer)
    if "payment_method" in paths and pi.payment_method_id:
        pm = _owned(db, PaymentMethod, pi.payment_method_id, pi.merchant_id)
        body["payment_method"] = cards.payment_method_json(pm)
    if "latest_charge" in paths and pi.latest_charge_id:
        charge = _owned(db, Charge, pi.latest_charge_id, pi.merchant_id)
        body["latest_charge"] = charge_json(db, charge)
    return body


def expand_charge(
    db: Session, charge: Charge, body: dict, paths: set[str], refunds: dict | None = None
) -> dict:
    if "refunds" in paths:
        if refunds is None:
            refunds = preload_charges(db, [charge], paths, charge.merchant_id)
        body["refunds"] = {
            "object": "list",
            "data": [refund_json(r) for r in refunds.get(charge.id, [])],
            "has_more": False,
            "url": f"/v1/charges/{charge.id}/refunds",
        }
    return body
