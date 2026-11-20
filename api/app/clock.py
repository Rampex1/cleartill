from datetime import UTC, datetime


def now() -> datetime:
    """The only source of 'now' for business logic; tests monkeypatch this."""
    return datetime.now(UTC)
