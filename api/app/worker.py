"""The worker process: `python -m app.worker` (design §5.1, §5.9)."""

import logging
import signal
import threading
import time

from app import config, delivery, housekeeping
from app.db import SessionLocal, engine
from app.ledger_check import ledger_check

log = logging.getLogger("cleartill.worker")
IDLE_SLEEP_SECONDS = 1.0
HOUSEKEEPING_INTERVAL = 60
DELIVERY_BUDGET_SECONDS = HOUSEKEEPING_INTERVAL  # a delivery backlog can't starve housekeeping
_stop = threading.Event()
_last_housekeeping: float | None = None


def check_ledger_on_start(bind) -> list[str]:
    """Reconcile once at startup; problems are CRITICAL but never stop the worker."""
    try:
        problems = ledger_check(bind)
    except Exception:
        log.critical("ledger check could not run at startup", exc_info=True)
        return ["ledger check could not run"]
    for problem in problems:
        log.critical("ledger check failed: %s", problem)
    return problems


def run_once(budget_seconds: float = DELIVERY_BUDGET_SECONDS) -> int:
    """Deliver due webhooks for at most `budget_seconds`; returns how many were attempted."""
    started, sent = time.monotonic(), 0
    while not _stop.is_set() and time.monotonic() - started < budget_seconds:
        if not delivery.deliver_one():
            break
        sent += 1
    return sent


def tick(now: float, session_factory=SessionLocal) -> bool:
    """One loop iteration: housekeeping first (once a minute), then webhook delivery. Neither
    can block or break the other. Returns True if housekeeping ran."""
    global _last_housekeeping
    ran = _last_housekeeping is None or now - _last_housekeeping >= HOUSEKEEPING_INTERVAL
    if ran:
        _last_housekeeping = now
        try:
            log.info("housekeeping %s", housekeeping.run_housekeeping(session_factory))
        except Exception:
            log.exception("housekeeping failed")
    try:
        run_once()
    except Exception:
        log.exception("webhook delivery failed")
    return ran


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    config.secret_key()
    config.public_base_url()
    config.warn_if_insecure_urls(log)
    # SIGTERM (deploys): finish the current delivery, then stop.
    signal.signal(signal.SIGTERM, lambda *_: _stop.set())
    log.info("worker started")
    check_ledger_on_start(engine)
    while not _stop.is_set():
        try:
            tick(time.monotonic())
        except Exception:
            log.exception("worker loop error")
        _stop.wait(IDLE_SLEEP_SECONDS)
    log.info("worker stopped")


if __name__ == "__main__":
    main()
