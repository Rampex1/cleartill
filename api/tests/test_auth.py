import base64
import hashlib
import re
from typing import Annotated

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import auth, clock, errors, models, seed
from app.db import get_db

t = FastAPI()
errors.install(t)


@t.get("/t/secret")
def secret(ctx: Annotated[object, Depends(auth.require_secret_key)]):
    return {"merchant": ctx.merchant.id, "kind": ctx.kind}


@t.get("/t/pk")
def pk(ctx: Annotated[object, Depends(auth.require_publishable_key)]):
    return {"merchant": ctx.merchant.id, "kind": ctx.kind}


@pytest.fixture
def tc(db):
    def override():
        yield db

    t.dependency_overrides[get_db] = override
    with TestClient(t, raise_server_exceptions=False) as c:
        yield c
    t.dependency_overrides.clear()


@pytest.fixture
def keys(db):
    return seed.seed(db, "a@example.com")


def basic(key):
    return {"Authorization": "Basic " + base64.b64encode(f"{key}:".encode()).decode()}


def bearer(key):
    return {"Authorization": f"Bearer {key}"}


def assert_invalid(r, sent=None):
    assert r.status_code == 401
    e = r.json()["error"]
    assert e["type"] == "invalid_request_error"
    assert e["code"] == "api_key_invalid"
    if sent:
        assert sent not in r.text


def test_basic_auth_with_secret_key(tc, keys):
    merchant, sk, _ = keys
    r = tc.get("/t/secret", headers=basic(sk))
    assert r.status_code == 200
    assert r.json()["merchant"] == merchant.id


def test_bearer_auth_with_secret_key(tc, keys):
    merchant, sk, _ = keys
    r = tc.get("/t/secret", headers=bearer(sk))
    assert r.status_code == 200
    assert r.json()["merchant"] == merchant.id


def test_missing_header_is_401(tc, keys):
    assert_invalid(tc.get("/t/secret"))


def test_empty_bearer_is_401(tc, keys):
    assert_invalid(tc.get("/t/secret", headers={"Authorization": "Bearer "}))


def test_bad_base64_is_401(tc, keys):
    assert_invalid(
        tc.get("/t/secret", headers={"Authorization": "Basic !!!notbase64"}), "!!!notbase64"
    )


def test_unknown_key_is_401(tc, keys):
    fake = "sk_test_" + "Z" * 43
    assert_invalid(tc.get("/t/secret", headers=bearer(fake)), fake)


def test_revoked_key_is_401(tc, db, keys):
    _, sk, _ = keys
    digest = hashlib.sha256(sk.encode()).hexdigest()
    row = db.scalar(select(models.ApiKey).where(models.ApiKey.key_hash == digest))
    row.revoked_at = clock.now()
    db.flush()
    assert_invalid(tc.get("/t/secret", headers=bearer(sk)), sk)


def test_publishable_key_on_secret_route_is_403(tc, keys):
    _, _, pkey = keys
    r = tc.get("/t/secret", headers=bearer(pkey))
    assert r.status_code == 403
    assert r.json()["error"]["type"] == "invalid_request_error"
    assert r.json()["error"]["code"] == "permission_denied"


def test_secret_key_on_publishable_route_is_403(tc, keys):
    _, sk, _ = keys
    r = tc.get("/t/pk", headers=bearer(sk))
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "permission_denied"


def test_publishable_key_on_publishable_route(tc, keys):
    merchant, _, pkey = keys
    r = tc.get("/t/pk", headers=bearer(pkey))
    assert r.status_code == 200
    assert r.json()["merchant"] == merchant.id


def test_keys_stored_hashed_only(db, keys):
    merchant, sk, pkey = keys
    rows = db.scalars(select(models.ApiKey).where(models.ApiKey.merchant_id == merchant.id)).all()
    assert len(rows) == 2
    by_kind = {row.kind: row for row in rows}
    for kind, key in (("secret", sk), ("publishable", pkey)):
        row = by_kind[kind]
        assert row.key_hash == hashlib.sha256(key.encode()).hexdigest()
        assert row.last4 == key[-4:]
        for col in models.ApiKey.__table__.columns:
            assert getattr(row, col.key) != key


def test_key_formats(keys):
    merchant, sk, pkey = keys
    assert re.fullmatch(r"sk_test_[0-9A-Za-z]{43}", sk)
    assert re.fullmatch(r"pk_test_[0-9A-Za-z]{43}", pkey)
    assert merchant.id.startswith("acct_")


def test_seed_twice_raises(db, keys):
    with pytest.raises(ValueError):
        seed.seed(db, "a@example.com")
