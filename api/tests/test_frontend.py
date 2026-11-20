"""M18: security headers on every response and the SPA served for browser navigations."""

from pathlib import Path

import pytest

WEB_DIST = Path(__file__).resolve().parents[2] / "web" / "dist"
CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; "
    "frame-ancestors 'none'; form-action 'self'"
)


def assert_security_headers(r):
    assert r.headers.get("content-security-policy") == CSP
    assert r.headers.get("referrer-policy") == "no-referrer"


def test_unknown_v1_route_is_json_404_with_security_headers(client):
    r = client.get("/v1/does-not-exist")
    assert r.status_code == 404
    assert r.json()["error"]["type"] == "invalid_request_error"
    assert_security_headers(r)


def test_healthz_has_security_headers(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    assert_security_headers(r)


def test_checkout_page_serves_spa_html_with_security_headers(client):
    if not (WEB_DIST / "index.html").exists():
        pytest.skip(f"SPA not built: {WEB_DIST / 'index.html'} is missing (run the web build)")
    r = client.get("/checkout/cs_x", headers={"Accept": "text/html"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert_security_headers(r)
