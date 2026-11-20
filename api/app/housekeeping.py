"""Periodic jobs run by the worker every minute (design §5.9). Each job takes a session and
doesn't commit; `run_housekeeping` gives every job its own transaction."""

import logging

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app import clock, executor, ledger, payment_intents
from app.db import SessionLocal
from app.models import BalanceTransaction, DashboardSession

log = logging.getLogger("cleartill.housekeeping")


# ponytail: one batch per minute (720k releases/day); loop batches if a backlog ever lags.
RELEASE_BATCH = 500


def release_due_funds(db: Session, limit: int = RELEASE_BATCH) -> int:
    """Pending → available for charges whose available_on has passed. Each row is released in
    its own savepoint, so one bad row is logged and skipped instead of blocking the batch."""
    due = db.scalars(
        select(BalanceTransaction)
        .where(
            BalanceTransaction.type == "charge",
            BalanceTransaction.status == "pending",
            BalanceTransaction.available_on <= clock.now(),
        )
        .order_by(BalanceTransaction.available_on)
        .limit(limit)
        .with_for_update(skip_locked=True)
    ).all()
    released = 0
    for bt in due:
        try:
            with db.begin_nested():
                released += ledger.release(db, bt)
        except Exception:
            log.exception("could not release balance transaction %s", bt.id)
    return released


def purge_dashboard_sessions(db: Session) -> int:
    result = db.execute(delete(DashboardSession).where(DashboardSession.expires_at < clock.now()))
    return result.rowcount


def _jobs() -> dict:
    # Looked up at call time, so tests (and future config) can swap a job.
    return {
        "released": release_due_funds,
        "expired_authorizations": payment_intents.expire_authorizations,
        "purged_idempotency_keys": executor.purge_idempotency_keys,
        "purged_sessions": purge_dashboard_sessions,
    }


def run_housekeeping(session_factory=SessionLocal) -> dict:
    """Run every job in its own transaction; a failing job is logged and doesn't stop the rest
    (its count is None)."""
    results = {}
    for name, job in _jobs().items():
        try:
            with session_factory.begin() as db:
                results[name] = job(db)
        except Exception:
            log.exception("housekeeping job failed: %s", name)
            results[name] = None
    return results
