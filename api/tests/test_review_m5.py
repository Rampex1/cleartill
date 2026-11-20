"""Regression tests for the M5 code and security review findings."""

import base64
import hashlib
from datetime import timedelta

import psycopg
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from starlette.requests import Request

from app import clock, merchants, passwords, ratelimit
from app.db import get_db
from app.main import app
from app.models import Merchant
from app.routers import dashboard

DASH = {"X-Cleartill-Dashboard": "1"}
FORM = {"Content-Type": "application/x-www-form-urlencoded"}


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    ratelimit.reset()
    monkeypatch.delenv("TRUSTED_PROXY_HOPS", raising=False)
    yield
    ratelimit.reset()


@pytest.fixture
def make_client(db):
    def override():
        yield db

    app.dependency_overrides[get_db] = override
    clients = []

    def make():
        c = TestClient(app, base_url="https://testserver")
        clients.append(c)
        return c

    yield make
    app.dependency_overrides.clear()


def signup(c, email="r5@example.com", password="correct horse"):
    r = c.post("/dashboard-api/signup", data={"email": email, "password": password}, headers=DASH)
    assert r.status_code == 200, r.text
    return r.json()


def key_rows_text(db):
    rows = db.execute(text("SELECT response_body::text FROM idempotency_keys")).scalars().all()
    return " ".join(r or "" for r in rows)


# --- secrets never stored for replay -------------------------------------------------------


def test_roll_with_idempotency_key_stores_no_secret(make_client, db):
    c = make_client()
    signup(c)
    keys = c.get("/dashboard-api/keys").json()["data"]
    sk_id = next(k["id"] for k in keys if k["kind"] == "secret")
    r = c.post(f"/dashboard-api/keys/{sk_id}/roll", headers={**DASH, "Idempotency-Key": "roll-1"})
    assert r.status_code == 200
    assert r.json()["secret"] not in key_rows_text(db)
    # a double-submitted roll does not mint a second key or replay the secret
    again = c.post(
        f"/dashboard-api/keys/{sk_id}/roll", headers={**DASH, "Idempotency-Key": "roll-1"}
    )
    assert again.status_code == 404 and "idempotent-replayed" not in again.headers


def test_signup_ignores_idempotency_key(make_client, db):
    c = make_client()
    r = c.post(
        "/dashboard-api/signup",
        data={"email": "ik@example.com", "password": "correct horse"},
        headers={**DASH, "Idempotency-Key": "s-1"},
    )
    assert r.status_code == 200
    assert r.json()["secret_key"] not in key_rows_text(db)
    assert db.execute(text("SELECT count(*) FROM idempotency_keys")).scalar() == 0


def test_cookie_auth_idempotency_replays_under_the_session_merchant(make_client):
    c = make_client()
    signup(c)
    h = {**DASH, **FORM, "Idempotency-Key": "dbl"}
    first = c.post("/v1/customers", content="email=a@x.com", headers=h)
    second = c.post("/v1/customers", content="email=a@x.com", headers=h)
    assert first.json()["id"] == second.json()["id"]
    assert second.headers["idempotent-replayed"] == "true"
    assert len(c.get("/v1/customers").json()["data"]) == 1


# --- expired cookie: no Basic challenge -------------------------------------------------


def test_expired_cookie_on_v1_is_401_without_basic_challenge(make_client, monkeypatch):
    c = make_client()
    signup(c)
    later = clock.now() + timedelta(days=7, seconds=1)
    monkeypatch.setattr(clock, "now", lambda: later)
    r = c.get("/v1/customers")
    assert r.status_code == 401 and r.json()["error"]["code"] == "not_authenticated"
    assert "www-authenticate" not in r.headers


def test_cookie_delete_on_v1_needs_csrf_header(make_client):
    c = make_client()
    signup(c)
    cus = c.post("/v1/customers", headers=DASH).json()["id"]
    assert c.delete(f"/v1/customers/{cus}").status_code == 403
    assert c.delete(f"/v1/customers/{cus}", headers=DASH).status_code == 200


# --- logout / login session hygiene -------------------------------------------------------


def test_logout_with_expired_session_still_clears_cookie(make_client, monkeypatch):
    c = make_client()
    signup(c)
    later = clock.now() + timedelta(days=8)
    monkeypatch.setattr(clock, "now", lambda: later)
    r = c.post("/dashboard-api/logout", headers=DASH)
    assert r.status_code == 200
    assert (
        "cleartill_session=" in r.headers["set-cookie"] and "Max-Age=0" in r.headers["set-cookie"]
    )


def test_logout_rejects_unknown_params(make_client):
    c = make_client()
    signup(c)
    r = c.post("/dashboard-api/logout", content="x=1", headers={**DASH, **FORM})
    assert r.status_code == 400 and r.json()["error"]["code"] == "parameter_unknown"


def test_roll_rejects_unknown_params(make_client):
    c = make_client()
    signup(c)
    sk_id = c.get("/dashboard-api/keys").json()["data"][0]["id"]
    r = c.post(f"/dashboard-api/keys/{sk_id}/roll", content="x=1", headers={**DASH, **FORM})
    assert r.status_code == 400


def test_login_replaces_the_presented_session(make_client, db):
    c = make_client()
    signup(c)
    before = db.execute(text("SELECT count(*) FROM dashboard_sessions")).scalar()
    r = c.post(
        "/dashboard-api/login",
        data={"email": "r5@example.com", "password": "correct horse"},
        headers=DASH,
    )
    assert r.status_code == 200
    assert db.execute(text("SELECT count(*) FROM dashboard_sessions")).scalar() == before


def test_login_upgrades_a_weak_password_hash(make_client, db):
    c = make_client()
    signup(c, email="weak@example.com")
    salt = b"0123456789abcdef"
    weak = hashlib.scrypt(b"correct horse", salt=salt, n=2**14, r=8, p=1, dklen=32)
    stored = (
        "scrypt$16384$8$1$"
        + base64.b64encode(salt).decode()
        + "$"
        + base64.b64encode(weak).decode()
    )
    m = db.scalar(select(Merchant).where(Merchant.email == "weak@example.com"))
    m.password_hash = stored
    db.flush()
    r = c.post(
        "/dashboard-api/login",
        data={"email": "weak@example.com", "password": "correct horse"},
        headers=DASH,
    )
    assert r.status_code == 200
    db.refresh(m)
    assert not passwords.needs_rehash(m.password_hash)
    assert passwords.verify_password("correct horse", m.password_hash)


def test_concurrent_duplicate_signup_is_account_exists(make_client, monkeypatch):
    def racing(*args, **kwargs):
        raise IntegrityError("INSERT", {}, psycopg.errors.UniqueViolation("dup"))

    monkeypatch.setattr(dashboard, "create_merchant", racing)
    r = make_client().post(
        "/dashboard-api/signup",
        data={"email": "race@example.com", "password": "correct horse"},
        headers=DASH,
    )
    assert r.status_code == 400 and r.json()["error"]["code"] == "account_exists"


def test_email_normalisation_is_unicode_aware():
    assert merchants.normalize_email(" ＭＥ@Ｘ.com ") == "me@x.com"
    assert merchants.normalize_email("Straße@x.com") == "strasse@x.com"


# --- rate limiter ---------------------------------------------------------------------------


def _req(xff_lines=(), client="9.9.9.9"):
    headers = [(b"x-forwarded-for", v.encode()) for v in xff_lines]
    return Request({"type": "http", "headers": headers, "client": (client, 1234)})


def test_client_ip_reads_every_xff_line(monkeypatch):
    monkeypatch.setenv("TRUSTED_PROXY_HOPS", "1")
    assert ratelimit.client_ip(_req(["6.6.6.6", "1.2.3.4"])) == "1.2.3.4"


def test_client_ip_with_two_hops(monkeypatch):
    monkeypatch.setenv("TRUSTED_PROXY_HOPS", "2")
    assert ratelimit.client_ip(_req(["spoof, 5.5.5.5, 10.0.0.1"])) == "5.5.5.5"


def test_bad_hops_value_ignores_xff_instead_of_failing(monkeypatch):
    monkeypatch.setenv("TRUSTED_PROXY_HOPS", "one")
    assert ratelimit.client_ip(_req(["1.2.3.4"])) == "9.9.9.9"


def test_non_ip_xff_entry_falls_back_to_client(monkeypatch):
    monkeypatch.setenv("TRUSTED_PROXY_HOPS", "1")
    assert ratelimit.client_ip(_req(["x" * 5000])) == "9.9.9.9"


def test_ipv6_addresses_in_one_64_share_a_bucket():
    for i in range(10):
        ratelimit.check("login", f"2001:db8::{i:x}")
    with pytest.raises(Exception) as exc:
        ratelimit.check("login", "2001:db8::ffff")
    assert getattr(exc.value, "status", None) == 429


def test_tracked_entries_are_bounded(monkeypatch):
    monkeypatch.setattr(ratelimit, "MAX_TRACKED", 5)
    t = [0.0]
    monkeypatch.setattr(ratelimit, "_now", lambda: t[0])
    for i in range(5):
        ratelimit.check("login", f"10.0.0.{i}")
    t[0] = 120.0  # all previous entries are now stale
    ratelimit.check("login", "10.0.1.1")
    assert len(ratelimit._hits) <= 2


def test_passwords_use_current_cost():
    assert passwords.hash_password("x" * 8).startswith(
        f"scrypt${passwords.N}${passwords.R}${passwords.P}$"
    )
    assert passwords.P >= 5
