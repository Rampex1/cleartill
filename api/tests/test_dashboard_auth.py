"""M5: dashboard signup/login/logout, session cookies, CSRF and API key list/roll."""

import re
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import clock, ratelimit, seed
from app.db import get_db
from app.main import app
from app.models import ApiKey, DashboardSession, Merchant

COOKIE = "cleartill_session"
CSRF = {"X-Cleartill-Dashboard": "1"}
PW = "hunter2hunter2"
HEX64 = re.compile(r"\b[0-9a-f]{64}\b")


@pytest.fixture(autouse=True)
def _reset_ratelimit():
    ratelimit.reset()
    yield
    ratelimit.reset()


@pytest.fixture
def make_client(db):
    """Clients on https://testserver so the Secure cookie is sent back."""

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    clients = []

    def make():
        c = TestClient(app, base_url="https://testserver")
        c.__enter__()
        clients.append(c)
        return c

    yield make
    for c in clients:
        c.__exit__(None, None, None)
    app.dependency_overrides.clear()


@pytest.fixture
def c(make_client):
    return make_client()


def err(r):
    return r.json()["error"]


def signup(c, email="me@x.com", password=PW, **extra):
    return c.post(
        "/dashboard-api/signup", data={"email": email, "password": password, **extra}, headers=CSRF
    )


def login(c, email="me@x.com", password=PW, headers=CSRF):
    return c.post(
        "/dashboard-api/login", data={"email": email, "password": password}, headers=headers
    )


def signed_up(c, email="me@x.com"):
    r = signup(c, email)
    assert r.status_code == 200, r.text
    return r.json()


def sessions(db, merchant_id):
    return db.scalars(
        select(DashboardSession).where(DashboardSession.merchant_id == merchant_id)
    ).all()


# --- signup --------------------------------------------------------------------------------


def test_signup_returns_merchant_and_keys(c):
    r = signup(c)
    assert r.status_code == 200, r.text
    body = r.json()
    assert re.match(r"^acct_", body["merchant"]["id"])
    assert body["merchant"]["email"] == "me@x.com"
    assert re.match(r"^sk_test_", body["secret_key"])
    assert re.match(r"^pk_test_", body["publishable_key"])


def test_signup_sets_hardened_cookie(c):
    r = signup(c)
    raw = r.headers["set-cookie"]
    assert raw.startswith(f"{COOKIE}=")
    assert "HttpOnly" in raw
    assert "Secure" in raw
    assert "samesite=lax" in raw.lower()
    assert "Max-Age=604800" in raw


def test_signup_stores_no_plaintext_password(c, db):
    body = signed_up(c)
    merchant = db.get(Merchant, body["merchant"]["id"])
    db.refresh(merchant)
    assert merchant.password_hash.startswith("scrypt$")
    assert PW not in merchant.password_hash


def test_signup_session_row_stores_token_hash_not_token(c, db):
    body = signed_up(c)
    rows = sessions(db, body["merchant"]["id"])
    assert len(rows) == 1
    assert re.fullmatch(r"[0-9a-f]{64}", rows[0].token_hash)
    assert rows[0].token_hash != c.cookies.get(COOKIE)


def test_signup_normalises_email(c, db):
    body = signed_up(c, "Me@X.com ")
    assert db.get(Merchant, body["merchant"]["id"]).email == "me@x.com"


def test_signup_duplicate_normalised_email_is_account_exists(c, make_client):
    signed_up(c, "Me@X.com ")
    r = signup(make_client(), "me@x.com")
    assert r.status_code == 400
    assert err(r)["code"] == "account_exists"
    assert err(r)["param"] == "email"


@pytest.mark.parametrize(
    "email",
    ["nope", "a b@c.d", "a" * (255 - len("@x.com")) + "@x.com"],
    ids=["no-at", "space", "255"],
)
def test_signup_invalid_email(c, email):
    r = signup(c, email)
    assert r.status_code == 400
    assert err(r)["param"] == "email"


@pytest.mark.parametrize("password", ["a" * 7, "a" * 129], ids=["7", "129"])
def test_signup_invalid_password_length(c, password):
    r = signup(c, password=password)
    assert r.status_code == 400
    assert err(r)["param"] == "password"


def test_signup_unknown_param(c):
    r = signup(c, foo="1")
    assert r.status_code == 400
    assert err(r)["code"] == "parameter_unknown"


# --- login ---------------------------------------------------------------------------------


def test_login_sets_cookie_and_creates_session(c, make_client, db):
    mid = signed_up(c)["merchant"]["id"]
    c2 = make_client()
    r = login(c2)
    assert r.status_code == 200, r.text
    assert r.json()["merchant"]["id"] == mid
    assert c2.cookies.get(COOKIE)
    assert COOKIE in r.headers["set-cookie"]
    assert len(sessions(db, mid)) == 2


def test_login_email_is_case_insensitive(c, make_client):
    signed_up(c, "me@x.com")
    r = login(make_client(), "ME@x.com")
    assert r.status_code == 200, r.text


def test_login_failures_are_indistinguishable(c, make_client):
    signed_up(c, "me@x.com")
    wrong = login(make_client(), "me@x.com", "wrong-password")
    unknown = login(make_client(), "ghost@x.com", "wrong-password")
    for r, email in ((wrong, "me@x.com"), (unknown, "ghost@x.com")):
        assert r.status_code == 401
        assert err(r)["code"] == "invalid_credentials"
        assert email not in r.text
    assert err(wrong)["message"] == err(unknown)["message"]


# --- CSRF ----------------------------------------------------------------------------------


def test_signup_without_csrf_header_is_403(c):
    r = c.post("/dashboard-api/signup", data={"email": "me@x.com", "password": PW})
    assert r.status_code == 403
    assert err(r)["code"] == "permission_denied"


def test_login_without_csrf_header_is_403(c, make_client):
    signed_up(c)
    r = login(make_client(), headers={})
    assert r.status_code == 403
    assert err(r)["code"] == "permission_denied"


def test_cookie_post_to_v1_without_csrf_header_is_403(c):
    signed_up(c)
    r = c.post("/v1/customers", data={"name": "x"})
    assert r.status_code == 403
    assert err(r)["code"] == "permission_denied"


def test_cookie_post_to_v1_with_csrf_header_is_200(c):
    signed_up(c)
    r = c.post("/v1/customers", data={"name": "x"}, headers=CSRF)
    assert r.status_code == 200, r.text


def test_cookie_get_on_v1_needs_no_csrf_header(c):
    signed_up(c)
    r = c.get("/v1/customers")
    assert r.status_code == 200, r.text


# --- precedence ----------------------------------------------------------------------------


def test_authorization_header_wins_over_cookie(c, db, make_client):
    signed_up(c, "a@x.com")
    _, sk_b, _ = seed.seed(db, "b@x.com")
    bearer_b = {"Authorization": f"Bearer {sk_b}"}
    r = c.post("/v1/customers", data={"name": "for-b"}, headers=bearer_b)
    assert r.status_code == 200, r.text
    cus = r.json()["id"]
    in_b = [x["id"] for x in make_client().get("/v1/customers", headers=bearer_b).json()["data"]]
    in_a = [x["id"] for x in c.get("/v1/customers").json()["data"]]
    assert cus in in_b
    assert cus not in in_a


# --- session lifetime ----------------------------------------------------------------------


def test_session_endpoint_returns_merchant(c):
    body = signed_up(c)
    r = c.get("/dashboard-api/session")
    assert r.status_code == 200, r.text
    assert r.json()["merchant"] == {"id": body["merchant"]["id"], "email": "me@x.com"}


def test_session_endpoint_without_cookie_is_401(c, make_client):
    signed_up(c)
    assert make_client().get("/dashboard-api/session").status_code == 401


def test_session_expires_after_seven_days(c, monkeypatch):
    signed_up(c)
    later = clock.now() + timedelta(days=7, seconds=1)
    monkeypatch.setattr(clock, "now", lambda: later)
    assert c.get("/dashboard-api/session").status_code == 401
    assert c.get("/v1/customers").status_code == 401


def test_garbage_cookie_is_401(c):
    r = c.get("/dashboard-api/session", headers={"Cookie": f"{COOKIE}=garbage"})
    assert r.status_code == 401


# --- logout --------------------------------------------------------------------------------


def test_logout_deletes_session_row(c, db):
    mid = signed_up(c)["merchant"]["id"]
    r = c.post("/dashboard-api/logout", headers=CSRF)
    assert r.status_code == 200, r.text
    assert sessions(db, mid) == []


def test_logout_invalidates_cookie_even_when_replayed(c, make_client):
    signed_up(c)
    old = c.cookies.get(COOKIE)
    assert c.post("/dashboard-api/logout", headers=CSRF).status_code == 200
    assert c.get("/dashboard-api/session").status_code == 401
    replay = make_client().get("/dashboard-api/session", headers={"Cookie": f"{COOKIE}={old}"})
    assert replay.status_code == 401


def test_logout_without_csrf_header_is_403(c):
    signed_up(c)
    r = c.post("/dashboard-api/logout")
    assert r.status_code == 403
    assert err(r)["code"] == "permission_denied"


# --- keys ----------------------------------------------------------------------------------


def test_list_keys_returns_both_kinds(c):
    signed_up(c)
    r = c.get("/dashboard-api/keys")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["object"] == "list"
    assert len(body["data"]) == 2
    assert {k["kind"] for k in body["data"]} == {"secret", "publishable"}
    for k in body["data"]:
        assert k["object"] == "api_key"
        assert {"id", "kind", "last4", "created"} <= set(k)
        assert "key_hash" not in k and "secret" not in k


def test_list_keys_leaks_no_secret_or_hash(c):
    body = signed_up(c)
    r = c.get("/dashboard-api/keys")
    assert body["secret_key"] not in r.text
    assert body["publishable_key"] not in r.text
    assert not HEX64.search(r.text)


def _key_id(c, kind):
    return next(k["id"] for k in c.get("/dashboard-api/keys").json()["data"] if k["kind"] == kind)


def test_roll_secret_key(c, make_client):
    old_sk = signed_up(c)["secret_key"]
    old_id = _key_id(c, "secret")
    r = c.post(f"/dashboard-api/keys/{old_id}/roll", headers=CSRF)
    assert r.status_code == 200, r.text
    new = r.json()
    assert re.match(r"^sk_test_", new["secret"])
    assert new["id"] != old_id
    assert new["kind"] == "secret"

    api = make_client()
    assert (
        api.get("/v1/customers", headers={"Authorization": f"Bearer {old_sk}"}).status_code == 401
    )
    assert (
        api.get("/v1/customers", headers={"Authorization": f"Bearer {new['secret']}"}).status_code
        == 200
    )

    data = c.get("/dashboard-api/keys").json()["data"]
    assert len(data) == 2
    assert old_id not in {k["id"] for k in data}
    assert new["id"] in {k["id"] for k in data}


def test_roll_revoked_key_is_404(c):
    signed_up(c)
    old_id = _key_id(c, "secret")
    assert c.post(f"/dashboard-api/keys/{old_id}/roll", headers=CSRF).status_code == 200
    assert c.post(f"/dashboard-api/keys/{old_id}/roll", headers=CSRF).status_code == 404


def test_roll_unknown_key_is_404(c):
    signed_up(c)
    assert c.post("/dashboard-api/keys/key_doesnotexist/roll", headers=CSRF).status_code == 404


def test_roll_other_merchants_key_is_404(c, db):
    signed_up(c, "a@x.com")
    merchant_b, sk_b, _ = seed.seed(db, "b@x.com")
    b_key = db.scalar(
        select(ApiKey.id).where(ApiKey.merchant_id == merchant_b.id, ApiKey.kind == "secret")
    )
    assert c.post(f"/dashboard-api/keys/{b_key}/roll", headers=CSRF).status_code == 404
    assert db.get(ApiKey, b_key).revoked_at is None


def test_roll_without_csrf_header_is_403(c):
    signed_up(c)
    r = c.post(f"/dashboard-api/keys/{_key_id(c, 'secret')}/roll")
    assert r.status_code == 403
    assert err(r)["code"] == "permission_denied"


def test_dashboard_api_rejects_api_key_auth(c, db):
    _, sk, _ = seed.seed(db, "b@x.com")
    r = c.get("/dashboard-api/keys", headers={"Authorization": f"Bearer {sk}"})
    assert r.status_code == 401
