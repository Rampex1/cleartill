from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session


def test_healthz_ok(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_healthz_db_unavailable(client):
    from app.db import get_db
    from app.main import app

    bad_engine = create_engine("postgresql+psycopg://x@127.0.0.1:1/x?connect_timeout=1")

    def broken_get_db():
        session = Session(bind=bad_engine)
        try:
            yield session
        finally:
            session.close()

    previous = app.dependency_overrides.copy()
    app.dependency_overrides[get_db] = broken_get_db
    try:
        response = client.get("/healthz")
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)
        bad_engine.dispose()

    assert response.status_code == 503
    assert response.json() == {"status": "unavailable"}


def test_commit_inside_test_stays_isolated(db, engine):
    db.execute(text("CREATE TABLE m1_probe(x int)"))
    db.execute(text("INSERT INTO m1_probe VALUES (1)"))
    db.commit()

    with engine.connect() as other:
        assert other.execute(text("SELECT to_regclass('m1_probe')")).scalar() is None
