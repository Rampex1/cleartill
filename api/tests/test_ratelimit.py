"""M5: per-IP rate limit on dashboard login and signup."""

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from app import ratelimit
from app.db import get_db
from app.main import app

CSRF = {"X-Cleartill-Dashboard": "1"}
BAD_LOGIN = {"email": "nobody@x.com", "password": "wrong-password"}


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.delenv("TRUSTED_PROXY_HOPS", raising=False)
    ratelimit.reset()
    yield
    ratelimit.reset()


@pytest.fixture
def c(db):
    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app, base_url="https://testserver") as client:
        yield client
    app.dependency_overrides.clear()


def login(c, headers=None):
    return c.post("/dashboard-api/login", data=BAD_LOGIN, headers={**CSRF, **(headers or {})})


def assert_limited(r):
    assert r.status_code == 429
    assert r.json()["error"]["type"] == "invalid_request_error"
    assert r.json()["error"]["code"] == "rate_limit"
    assert "retry-after" in r.headers


def test_login_eleventh_attempt_is_429(c):
    for _ in range(10):
        assert login(c).status_code == 401
    assert_limited(login(c))


def test_spoofed_forwarded_for_does_not_bypass_login_limit(c):
    for i in range(10):
        assert login(c, {"X-Forwarded-For": f"10.0.0.{i}"}).status_code == 401
    assert_limited(login(c, {"X-Forwarded-For": "10.0.0.99"}))


def test_signup_eleventh_attempt_is_429(c):
    for _ in range(10):
        r = c.post("/dashboard-api/signup", data={"email": "nope", "password": "x"}, headers=CSRF)
        assert r.status_code == 400
    assert_limited(
        c.post("/dashboard-api/signup", data={"email": "nope", "password": "x"}, headers=CSRF)
    )


def test_limit_window_expires(c, monkeypatch):
    t = 1000.0
    monkeypatch.setattr(ratelimit, "_now", lambda: t)
    for _ in range(10):
        assert login(c).status_code == 401
    monkeypatch.setattr(ratelimit, "_now", lambda: t + 61)
    assert login(c).status_code != 429


def _request(xff=None):
    headers = [(b"x-forwarded-for", xff.encode())] if xff is not None else []
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "query_string": b"",
            "headers": headers,
            "client": ("9.9.9.9", 1234),
        }
    )


def test_client_ip_ignores_forwarded_for_without_trusted_hops():
    assert ratelimit.client_ip(_request("spoofed, 1.2.3.4")) == "9.9.9.9"


def test_client_ip_uses_nth_from_right_with_trusted_hops(monkeypatch):
    monkeypatch.setenv("TRUSTED_PROXY_HOPS", "1")
    assert ratelimit.client_ip(_request("spoofed, 1.2.3.4")) == "1.2.3.4"


def test_client_ip_falls_back_to_client_host_without_forwarded_for(monkeypatch):
    monkeypatch.setenv("TRUSTED_PROXY_HOPS", "1")
    assert ratelimit.client_ip(_request()) == "9.9.9.9"
