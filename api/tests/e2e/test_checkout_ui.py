"""M18: the hosted checkout SPA in a real browser against a live server (committed data;
rows stay behind)."""

import re
import socket
import threading
import time
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlencode

import pytest

WEB_DIST = Path(__file__).resolve().parents[3] / "web" / "dist"
if not (WEB_DIST / "index.html").exists():
    pytest.skip(
        f"SPA not built: {WEB_DIST / 'index.html'} is missing (run the web build)",
        allow_module_level=True,
    )

import uvicorn  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from playwright.sync_api import expect, sync_playwright  # noqa: E402

from app import config, seed  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.ids import new_id  # noqa: E402
from app.main import app  # noqa: E402

FORM = {"Content-Type": "application/x-www-form-urlencoded"}
EXP_YEAR = str(datetime.now(UTC).year + 5)
VISA = "4242424242424242"
DECLINE = "4000000000000002"
THREE_DS = "4000002760003184"
BANNER = "Test mode: simulated payments, never enter a real card."
CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; "
    "frame-ancestors 'none'; form-action 'self'"
)


@pytest.fixture(scope="module")
def base_url(engine):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("PUBLIC_BASE_URL", base)
        config.public_base_url.cache_clear()
        server = uvicorn.Server(
            uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
        )
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        deadline = time.monotonic() + 15
        while True:
            try:
                with urllib.request.urlopen(f"{base}/healthz", timeout=1) as r:
                    if r.status == 200:
                        break
            except OSError:
                pass
            assert time.monotonic() < deadline, "live server did not start"
            time.sleep(0.05)
        try:
            yield base
        finally:
            server.should_exit = True
            thread.join(10)
    config.public_base_url.cache_clear()


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()


@pytest.fixture
def page(browser, base_url):
    context = browser.new_context()
    page = context.new_page()
    console = []
    page.on("console", lambda m: console.append(m.text))
    page.route(
        "https://merchant.test/**",
        lambda route: route.fulfill(
            status=200, content_type="text/html", body="<html><body>merchant ok</body></html>"
        ),
    )
    yield page
    context.close()
    bad = [m for m in console if "Content Security Policy" in m or "Refused to" in m]
    assert bad == [], bad


@pytest.fixture
def new_session(base_url):
    """Factory: commit a merchant and a 2 x 1500 USD session; return (sk headers, session).
    Teardown pays any session the test left open: test_checkout's expiry tests count every
    due open session, so committed open ones would leak into them."""
    created = []

    def make():
        skh, cs = create_session(base_url)
        created.append((skh, cs))
        return skh, cs

    yield make
    for skh, cs in created:
        if session_status(skh, cs["id"]) == "open":
            data = {
                "card[number]": VISA,
                "card[exp_month]": "12",
                "card[exp_year]": EXP_YEAR,
                "card[cvc]": "123",
            }
            with TestClient(app) as c:
                r = c.post(
                    f"/checkout-api/sessions/{cs['id']}/pay", content=urlencode(data), headers=FORM
                )
            assert r.status_code == 200, r.text
            assert session_status(skh, cs["id"]) == "complete"


def create_session(base_url):
    with SessionLocal.begin() as s:
        _, sk, _ = seed.seed(s, f"{new_id('ui_')}@example.com")
    skh = {"Authorization": f"Bearer {sk}", **FORM}
    body = {
        "mode": "payment",
        "line_items[0][price_data][currency]": "usd",
        "line_items[0][price_data][unit_amount]": "1500",
        "line_items[0][price_data][product_data][name]": "Tee",
        "line_items[0][quantity]": "2",
        "success_url": "https://merchant.test/ok?cs={CHECKOUT_SESSION_ID}",
        "cancel_url": "https://merchant.test/back",
    }
    with TestClient(app) as c:
        r = c.post("/v1/checkout/sessions", content=urlencode(body), headers=skh)
    assert r.status_code == 200, r.text
    cs = r.json()
    assert cs["url"] == f"{base_url}/checkout/{cs['id']}"
    return skh, cs


def session_status(skh, cs_id):
    with TestClient(app) as c:
        r = c.get(f"/v1/checkout/sessions/{cs_id}", headers=skh)
    assert r.status_code == 200, r.text
    return r.json()["status"]


def fill_and_pay(page, number):
    page.get_by_label("Card number").fill(number)
    page.get_by_label("Expiry month").fill("12")
    page.get_by_label("Expiry year").fill(EXP_YEAR)
    page.get_by_label("CVC").fill("123")
    page.get_by_role("button", name="Pay $30.00").click()


def success_url(cs):
    return f"https://merchant.test/ok?cs={cs['id']}"


def test_pay_with_4242_redirects_to_success_url(page, base_url, new_session):
    skh, cs = new_session()
    page.goto(cs["url"])
    expect(page.get_by_text(BANNER)).to_be_visible()
    expect(page.get_by_text("Tee")).to_be_visible()
    expect(page.get_by_role("button", name="Pay $30.00")).to_be_visible()
    fill_and_pay(page, VISA)
    page.wait_for_url(success_url(cs))
    expect(page.get_by_text("merchant ok")).to_be_visible()
    assert session_status(skh, cs["id"]) == "complete"


def test_declined_card_shows_alert_and_stays_on_checkout(page, base_url, new_session):
    skh, cs = new_session()
    page.goto(cs["url"])
    fill_and_pay(page, DECLINE)
    expect(page.get_by_role("alert")).to_contain_text("Your card was declined.")
    assert page.url == cs["url"]
    assert session_status(skh, cs["id"]) == "open"


def test_3ds_approve_completes_session(page, base_url, new_session):
    skh, cs = new_session()
    page.goto(cs["url"])
    fill_and_pay(page, THREE_DS)
    page.wait_for_url(re.compile(re.escape(f"{base_url}/checkout/3ds/") + r".*client_secret="))
    expect(page.get_by_text(BANNER)).to_be_visible()
    page.get_by_role("button", name="Complete authentication").click()
    page.wait_for_url(success_url(cs))
    assert session_status(skh, cs["id"]) == "complete"


def test_3ds_fail_returns_to_checkout_with_alert_then_pays(page, base_url, new_session):
    skh, cs = new_session()
    page.goto(cs["url"])
    fill_and_pay(page, THREE_DS)
    page.wait_for_url(re.compile(re.escape(f"{base_url}/checkout/3ds/")))
    page.get_by_role("button", name="Fail authentication").click()
    page.wait_for_url(re.compile(re.escape(cs["url"]) + r"(\?.*)?$"))
    expect(page.get_by_role("alert")).to_be_visible()
    assert session_status(skh, cs["id"]) == "open"
    fill_and_pay(page, VISA)
    page.wait_for_url(success_url(cs))
    assert session_status(skh, cs["id"]) == "complete"


def test_checkout_document_has_security_headers(page, base_url, new_session):
    _, cs = new_session()
    response = page.goto(cs["url"])
    assert response is not None and response.status == 200
    headers = response.all_headers()
    assert headers.get("content-security-policy") == CSP
    assert headers.get("referrer-policy") == "no-referrer"
