"""One transaction per /v1 write, with Stripe-style Idempotency-Key handling inside it.

Routes call `execute(db, request, merchant_id, params, handler)`. The handler returns a dict
(200) or a `Result`, and raises `ApiError` for 4xx. The executor stores the response on the
idempotency key in the same transaction as the handler's writes, commits, and only then
returns. Anything raised rolls back this request's savepoint, which also frees the key.
"""

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta

from fastapi import Request
from fastapi.responses import JSONResponse
from sqlalchemy import delete, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app import clock
from app.errors import ApiError
from app.models import IdempotencyKey

LOCK_TIMEOUT = "5s"
MAX_KEY_LENGTH = 255
CLAIM_ATTEMPTS = 3
REPLAY_HEADER = "Idempotent-Replayed"
LOCK_NOT_AVAILABLE = "55P03"


@dataclass
class Result:
    """A non-200 response that must still commit (e.g. a card decline)."""

    status: int
    body: dict


def request_hash(request: Request, params: dict) -> str:
    canonical = json.dumps(params, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(f"{request.method} {request.url.path} {canonical}".encode()).hexdigest()


def _load_key(db: Session, merchant_id: str, key: str):
    # Core select (no identity map): always the committed state visible right now.
    return db.execute(
        select(
            IdempotencyKey.request_hash,
            IdempotencyKey.response_status,
            IdempotencyKey.response_body,
        ).where(IdempotencyKey.merchant_id == merchant_id, IdempotencyKey.key == key)
    ).first()


def _in_use() -> ApiError:
    return ApiError(
        409,
        "idempotency_error",
        "Another request with this Idempotency-Key is still running; retry it shortly.",
        code="idempotency_key_in_use",
    )


def _claim(db: Session, merchant_id: str, key: str, req_hash: str):
    """Insert the key, or return the stored row to replay.

    A concurrent request holding the same key makes the insert wait on the unique index:
    when it commits we replay its response; when it rolls back our insert goes through."""
    for _ in range(CLAIM_ATTEMPTS):
        try:
            inserted = db.execute(
                insert(IdempotencyKey)
                .values(
                    merchant_id=merchant_id, key=key, request_hash=req_hash, created=clock.now()
                )
                .on_conflict_do_nothing(index_elements=["merchant_id", "key"])
                .returning(IdempotencyKey.key)
            ).first()
        except OperationalError as exc:
            # ponytail: assumes the only lock this insert can wait on is the key row. If a later
            # path locks `merchants` FOR UPDATE (FK target), use FOR NO KEY UPDATE there.
            if getattr(exc.orig, "sqlstate", None) == LOCK_NOT_AVAILABLE:
                raise _in_use() from None
            raise
        if inserted:
            return None
        stored = _load_key(db, merchant_id, key)
        if stored is None:
            continue  # purged between our insert and select: try the insert again
        if stored.request_hash != req_hash:
            raise ApiError(
                400,
                "idempotency_error",
                "This Idempotency-Key was used with a different request; use a new key.",
                code="idempotency_key_reused",
            )
        if stored.response_status is None:
            raise _in_use()
        return stored
    raise _in_use()


def execute(
    db: Session,
    request: Request,
    merchant_id: str | None,
    params: dict,
    handler: Callable[[], dict | Result],
) -> JSONResponse:
    # Stripe: keys apply to POST only; GET and DELETE ignore them.
    # Keys are stored per merchant, so unauthenticated calls (signup, login) take none.
    use_key = request.method == "POST" and merchant_id is not None
    key = request.headers.get("idempotency-key") if use_key else None
    headers = {}
    # A savepoint per request: on any exception only this request's writes (and its key) are
    # undone, and the enclosing transaction (auth's read; in tests, the test's own data) stays
    # usable. On success the commit below makes everything durable at once.
    savepoint = db.begin_nested()
    with savepoint:
        db.execute(text("SELECT set_config('lock_timeout', :t, true)"), {"t": LOCK_TIMEOUT})
        stored = None
        if key is not None:
            if not 1 <= len(key) <= MAX_KEY_LENGTH:
                raise ApiError(
                    400,
                    "invalid_request_error",
                    f"Idempotency-Key must be 1 to {MAX_KEY_LENGTH} characters.",
                    code="idempotency_key_invalid",
                )
            stored = _claim(db, merchant_id, key, request_hash(request, params))
        if stored is not None:
            status, body = stored.response_status, stored.response_body
            headers[REPLAY_HEADER] = "true"
        else:
            out = handler()
            if not savepoint.is_active:
                # A handler that commits would store a key without its response.
                raise RuntimeError("handlers must not commit or roll back; the executor does")
            status, body = (out.status, out.body) if isinstance(out, Result) else (200, out)
            if key is not None:
                db.execute(
                    update(IdempotencyKey)
                    .where(IdempotencyKey.merchant_id == merchant_id, IdempotencyKey.key == key)
                    .values(response_status=status, response_body=body)
                )
    # Serialise before committing, so an unserialisable body can't 500 after a durable write.
    response = JSONResponse(body, status_code=status, headers=headers)
    db.commit()  # the response is returned only after this succeeds
    return response


def purge_idempotency_keys(db: Session, older_than: timedelta = timedelta(hours=24)) -> int:
    """Delete keys older than `older_than` (Stripe keeps them at least 24h). The caller
    commits; run by the worker (M15). ponytail: one DELETE; batch it if the table grows."""
    result = db.execute(
        delete(IdempotencyKey).where(IdempotencyKey.created < clock.now() - older_than)
    )
    return result.rowcount
