"""Tenant-scoped loading and cursor pagination. Every object lookup goes through here."""

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from app.errors import ApiError
from app.forms import ListParams

MAX_ID_ECHO = 100


def _missing(model, object_id: str, param: str | None) -> ApiError:
    return ApiError(
        404,
        "invalid_request_error",
        f"No such {model.object_name}: '{object_id[:MAX_ID_ECHO]}'",
        code="resource_missing",
        param=param,
    )


def get_owned(
    db: Session,
    model,
    object_id: str,
    merchant_id: str,
    *,
    param: str | None = None,
    for_update: bool = False,
    include_deleted: bool = False,
):
    """Load `model` by id for this merchant; anything else is a 404 (never a 403).

    `for_update` must be the first load of the row in the session: `populate_existing`
    refreshes attributes that the identity map would otherwise hand back stale."""
    stmt = select(model).where(model.id == object_id, model.merchant_id == merchant_id)
    if not include_deleted and hasattr(model, "deleted"):
        stmt = stmt.where(model.deleted.is_(False))
    if for_update:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    obj = db.scalar(stmt)
    if obj is None:
        raise _missing(model, object_id, param)
    return obj


def paginate(db: Session, stmt: Select, model, merchant_id: str, lp: ListParams, url: str) -> dict:
    """Stripe-style cursor page over `stmt` (already filtered), newest first by `seq`.

    `data` holds model rows; the caller serialises them."""
    if lp.starting_after and lp.ending_before:
        raise ApiError(
            400,
            "invalid_request_error",
            "starting_after and ending_before can't be used together.",
            code="parameter_invalid",
            param="ending_before",
        )
    param = "starting_after" if lp.starting_after else "ending_before"
    cursor_id = lp.starting_after or lp.ending_before
    if cursor_id is not None:
        # Cursors may point at rows the list now hides (e.g. deleted ones).
        cursor_seq = db.scalar(
            select(model.seq).where(model.id == cursor_id, model.merchant_id == merchant_id)
        )
        if cursor_seq is None:
            raise _missing(model, cursor_id, param)
    if lp.starting_after:
        stmt = stmt.where(model.seq < cursor_seq).order_by(model.seq.desc())
    elif lp.ending_before:
        stmt = stmt.where(model.seq > cursor_seq).order_by(model.seq.asc())
    else:
        stmt = stmt.order_by(model.seq.desc())
    rows = list(db.scalars(stmt.limit(lp.limit + 1)))
    has_more = len(rows) > lp.limit
    rows = rows[: lp.limit]
    if lp.ending_before:
        rows.reverse()
    return {"object": "list", "data": rows, "has_more": has_more, "url": url}
