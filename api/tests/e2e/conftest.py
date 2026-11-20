from pathlib import Path

E2E_DIR = Path(__file__).resolve().parent


def pytest_collection_modifyitems(items):
    """Run browser tests last: they commit rows (payment methods, sessions) through a live
    server, and some earlier tests count committed rows table-wide (test_card_data)."""
    items.sort(key=lambda item: E2E_DIR in Path(item.path).parents)
