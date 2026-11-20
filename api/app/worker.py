"""The worker process: `python -m app.worker` (design §5.1). M15 adds housekeeping."""

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
_stop = threading.Event()
_last_housekeeping: float | None = None


def check_ledger_on_start(bind) -> list[str]:
    """Reconcile once at startup; problems are CRITICAL but don't crash-loop the worker."""
    problems = ledger_check(bind)
    for problem in problems:
        log.critical("ledger check failed: %s", problem)
    return problems


def run_once() -> int:
    """Deliver everything that's due; returns how many deliveries were attempted."""
    sent = 0
    while not _stop.is_set() and delivery.deliver_one():
        sent += 1
    return sent


def tick(now: float, session_factory=SessionLocal) -> bool:
    """One loop iteration: deliver due webhooks, then housekeeping if a minute has passed.
    Returns True if housekeeping ran."""
    global _last_housekeeping
    run_once()
    if _last_housekeeping is not None and now - _last_housekeeping < HOUSEKEEPING_INTERVAL:
        return False
    _last_housekeeping = now
    results = housekeeping.run_housekeeping(session_factory)
    if any(results.values()):
        log.info("housekeeping %s", results)
    return True


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    config.secret_key()
    config.public_base_url()
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
