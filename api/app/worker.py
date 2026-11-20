"""The worker process: `python -m app.worker` (design §5.1). M15 adds housekeeping."""

import logging
import signal
import threading

from app import config, delivery

log = logging.getLogger("cleartill.worker")
IDLE_SLEEP_SECONDS = 1.0
_stop = threading.Event()


def run_once() -> int:
    """Deliver everything that's due; returns how many deliveries were attempted."""
    sent = 0
    while not _stop.is_set() and delivery.deliver_one():
        sent += 1
    return sent


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    config.secret_key()
    config.public_base_url()
    # SIGTERM (deploys): finish the current delivery, then stop.
    signal.signal(signal.SIGTERM, lambda *_: _stop.set())
    log.info("worker started")
    while not _stop.is_set():
        try:
            run_once()
        except Exception:
            log.exception("worker loop error")
        _stop.wait(IDLE_SLEEP_SECONDS)
    log.info("worker stopped")


if __name__ == "__main__":
    main()
