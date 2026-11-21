from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime

# Set only by `at()`: lets the demo seeder run real business logic "as of" a past moment,
# so every row it writes (journal entries included) carries a believable timestamp.
_as_of: ContextVar[datetime | None] = ContextVar("cleartill_clock_as_of", default=None)


def now() -> datetime:
    """The only source of 'now' for business logic; tests monkeypatch this."""
    frozen = _as_of.get()
    return frozen if frozen is not None else datetime.now(UTC)


@contextmanager
def at(moment: datetime):
    """Run a block as of `moment` (context-local, so concurrent requests are unaffected)."""
    token = _as_of.set(moment)
    try:
        yield
    finally:
        _as_of.reset(token)
