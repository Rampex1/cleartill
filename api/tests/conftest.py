import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from testcontainers.community.postgres import PostgresContainer

API_DIR = Path(__file__).resolve().parent.parent
_container = PostgresContainer("postgres:16", driver="psycopg")
_saved_database_url = None


def pytest_configure(config):
    os.environ.setdefault("SECRET_KEY", "test-secret-0123456789abcdef0123456789abcdef")
    # Ryuk (testcontainers' reaper) intermittently fails to start locally; pytest_unconfigure
    # already stops our one container.
    if not os.environ.get("CI"):
        os.environ.setdefault("TESTCONTAINERS_RYUK_DISABLED", "true")
    # Start Postgres before collection: app.db reads DATABASE_URL at import time, so test
    # modules may then `from app.main import app` at the top level.
    global _saved_database_url
    _saved_database_url = os.environ.get("DATABASE_URL")
    _container.start()
    os.environ["DATABASE_URL"] = _container.get_connection_url()
    command.upgrade(Config(str(API_DIR / "alembic.ini")), "head")


def pytest_unconfigure(config):
    _container.stop()
    if _saved_database_url is None:
        os.environ.pop("DATABASE_URL", None)
    else:
        os.environ["DATABASE_URL"] = _saved_database_url


@pytest.fixture(scope="session")
def pg():
    return os.environ["DATABASE_URL"]


@pytest.fixture(scope="session")
def engine(pg):
    eng = create_engine(pg)
    yield eng
    eng.dispose()


@pytest.fixture
def db(engine):
    conn = engine.connect()
    trans = conn.begin()
    session = Session(bind=conn, join_transaction_mode="create_savepoint")
    yield session
    session.close()
    trans.rollback()
    conn.close()


@pytest.fixture
def client(db):
    from app.db import get_db
    from app.main import app

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
