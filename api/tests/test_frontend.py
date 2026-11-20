"""M18: security headers on every response and the SPA served for browser navigations."""

from pathlib import Path

import pytest

from app.frontend import WEB_DIST as _WEB_DIST

WEB_DIST = Path(_WEB_DIST)  # what the app actually serves (WEB_DIST env or <repo>/web/dist)
CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; "
    "frame-ancestors 'none'; form-action 'self'; base-uri 'none'"
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


def test_unknown_api_path_is_json_even_for_html_clients(client):
    for path in ("/v1/does-not-exist", "/checkout-api/nope", "/dashboard-api/nope"):
        r = client.get(path, headers={"Accept": "text/html,*/*"})
        assert r.status_code == 404, path
        assert r.json()["error"]["type"] == "invalid_request_error"


def test_api_docs_are_exempt_from_the_csp(client):
    r = client.get("/docs")
    assert r.status_code == 200
    assert "content-security-policy" not in r.headers
    assert r.headers["referrer-policy"] == "no-referrer"


def test_index_is_revalidated_and_assets_are_immutable(client):
    if not (WEB_DIST / "index.html").exists():
        pytest.skip("SPA not built")
    page = client.get("/checkout/cs_x", headers={"Accept": "text/html"})
    assert page.headers["cache-control"] == "no-cache"
    asset = next((WEB_DIST / "assets").glob("*.js")).name
    r = client.get(f"/assets/{asset}")
    assert r.status_code == 200
    assert r.headers["cache-control"] == "public, max-age=31536000, immutable"
