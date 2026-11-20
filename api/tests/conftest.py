import os
import sys
from pathlib import Path

import pytest
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from testcontainers.postgres import PostgresContainer

from alembic import command

API_DIR = Path(__file__).resolve().parent.parent
# The `pytest` entrypoint does not put the project root on sys.path.
sys.path.insert(0, str(API_DIR))


@pytest.fixture(scope="session")
def pg():
    with PostgresContainer("postgres:16", driver="psycopg") as container:
        url = container.get_connection_url()
        os.environ["DATABASE_URL"] = url
        command.upgrade(Config(str(API_DIR / "alembic.ini")), "head")
        yield url


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
