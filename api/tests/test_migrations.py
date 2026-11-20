from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import text

API_DIR = Path(__file__).resolve().parent.parent


def test_downgrade_then_upgrade_creates_tables(engine):
    cfg = Config(str(API_DIR / "alembic.ini"))
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    with engine.connect() as conn:
        for table in ("merchants", "api_keys"):
            assert conn.execute(text(f"SELECT to_regclass('{table}')")).scalar() == table
