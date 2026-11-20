"""The worker process: `python -m app.worker` (design §5.1). M15 adds housekeeping."""

import logging
import time

from app import config, delivery

log = logging.getLogger("cleartill.worker")
IDLE_SLEEP_SECONDS = 1.0


def run_once() -> int:
    """Deliver everything that's due; returns how many deliveries were attempted."""
    sent = 0
    while delivery.deliver_one():
        sent += 1
    return sent


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    config.secret_key()
    config.public_base_url()
    log.info("worker started")
    while True:
        try:
            run_once()
        except Exception:
            log.exception("worker loop error")
        time.sleep(IDLE_SLEEP_SECONDS)


if __name__ == "__main__":
    main()
