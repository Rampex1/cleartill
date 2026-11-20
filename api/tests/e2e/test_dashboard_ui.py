# ruff: noqa: F811  (pytest fixtures shared from test_checkout_ui)
"""M19: the merchant dashboard SPA (signup, login, payments, capture/refund, API keys) in a real
browser against a live server (committed data; rows stay behind).

The session cookie is Secure, and Chromium only keeps Secure cookies over plain http for
localhost, so the browser talks to the live server as http://localhost:<port>."""

import re
from urllib.parse import urlencode

import pytest
from fastapi.testclient import TestClient
from playwright.sync_api import expect

# Fixtures (and the module-level skip when the SPA isn't built) come from the checkout tests.
from test_checkout_ui import BANNER, FORM, base_url, browser, page  # noqa: F401

from app import ratelimit
from app.db import SessionLocal
from app.ids import new_id
from app.main import app
from app.merchants import create_merchant

PW = "hunter2hunter2"


@pytest.fixture(autouse=True)
def _reset_ratelimit():
    ratelimit.reset()
    yield
    ratelimit.reset()


@pytest.fixture
def ui(base_url):  # noqa: F811
    return base_url.replace("127.0.0.1", "localhost")


def email():
    return f"{new_id('dash_').lower()}@example.com"


def committed_merchant():
    addr = email()
    with SessionLocal.begin() as s:
        merchant, sk, _ = create_merchant(s, addr, password=PW)
        merchant_id = merchant.id
    return merchant_id, addr, sk


def sk_headers(sk):
    return {"Authorization": f"Bearer {sk}", **FORM}


def manual_capture_pi(sk):
    body = {
        "amount": "2000",
        "currency": "usd",
        "capture_method": "manual",
        "payment_method": "pm_card_visa",
        "confirm": "true",
    }
    with TestClient(app) as c:
        r = c.post("/v1/payment_intents", content=urlencode(body), headers=sk_headers(sk))
    assert r.status_code == 200, r.text
    pi = r.json()
    assert pi["status"] == "requires_capture"
    return pi


def log_in(page, ui, addr, password=PW):  # noqa: F811
    page.goto(f"{ui}/dashboard/login")
    page.get_by_label("Email").fill(addr)
    page.get_by_label("Password").fill(password)
    page.get_by_role("button", name="Log in").click()


def logged_in(page, ui, addr):  # noqa: F811
    log_in(page, ui, addr)
    expect(page.get_by_role("button", name="Log out")).to_be_visible()


def open_payment(page, ui, pi_id):  # noqa: F811
    page.get_by_role("link", name="Payments").click()
    expect(page.get_by_role("heading", name="Payments")).to_be_visible()
    link = page.get_by_role("link", name=pi_id)
    expect(link).to_be_visible()
    link.click()
    page.wait_for_url(f"{ui}/dashboard/payments/{pi_id}")
    expect(page.get_by_role("heading", name="Payment", exact=True)).to_be_visible()


def status(page, value):  # noqa: F811
    """The PI's Status field only (the charge line also says "succeeded")."""
    return page.locator('dt:text-is("Status") + dd').filter(has_text=re.compile(rf"^{value}$"))


def refunds_for(sk, pi_id):
    with TestClient(app) as c:
        r = c.get(f"/v1/refunds?payment_intent={pi_id}&limit=100", headers=sk_headers(sk))
    assert r.status_code == 200, r.text
    return r.json()["data"]


def to_login(ui):
    return re.compile(re.escape(f"{ui}/dashboard/login") + r"(\?.*)?$")


def test_signup_shows_keys_then_continues_to_empty_payments(page, ui):  # noqa: F811
    page.goto(f"{ui}/dashboard/login")
    page.get_by_role("link", name="Create an account").click()
    page.wait_for_url(f"{ui}/dashboard/signup")
    page.get_by_label("Email").fill(email())
    page.get_by_label("Password").fill(PW)
    page.get_by_role("button", name="Create account").click()
    expect(page.get_by_role("heading", name="Your API keys")).to_be_visible()
    text = page.locator("body").inner_text()
    assert re.search(r"sk_test_\w+", text), "secret key not shown"
    assert re.search(r"pk_test_\w+", text), "publishable key not shown"
    page.get_by_role("button", name="Continue to dashboard").click()
    page.wait_for_url(f"{ui}/dashboard/payments")
    expect(page.get_by_role("heading", name="Payments")).to_be_visible()
    expect(page.get_by_text(BANNER)).to_be_visible()
    expect(page.get_by_role("link", name="API keys")).to_be_visible()
    expect(page.get_by_role("link", name=re.compile(r"^pi_"))).to_have_count(0)


def test_login_capture_and_refund_payment(page, ui):  # noqa: F811
    _, addr, sk = committed_merchant()
    pi = manual_capture_pi(sk)
    logged_in(page, ui, addr)
    expect(page.get_by_text(BANNER)).to_be_visible()
    open_payment(page, ui, pi["id"])
    expect(status(page, "requires_capture")).to_be_visible()

    page.get_by_role("button", name="Capture").click()
    expect(status(page, "succeeded")).to_be_visible()
    expect(page.get_by_role("button", name="Capture")).to_have_count(0)

    page.get_by_label("Refund amount").fill("500")
    page.get_by_role("button", name="Refund").click()
    expect(page.get_by_role("listitem").filter(has_text="Refunded $5.00")).to_be_visible()

    page.get_by_label("Refund amount").fill("")
    page.get_by_role("button", name="Refund").click()
    refunded = page.get_by_role("listitem").filter(has_text="Refunded")
    expect(refunded).to_have_count(2)
    expect(refunded.filter(has_text="Refunded $15.00")).to_be_visible()

    refunds = refunds_for(sk, pi["id"])
    assert len(refunds) == 2, refunds
    assert sum(r["amount"] for r in refunds) == 2000
    page.reload()
    expect(page.get_by_role("listitem").filter(has_text="Refunded")).to_have_count(2)


def test_wrong_password_shows_alert_and_stays_on_login(page, ui):  # noqa: F811
    _, addr, _ = committed_merchant()
    log_in(page, ui, addr, password="not-the-password")
    expect(page.get_by_role("alert")).to_contain_text("Invalid email or password.")
    assert page.url == f"{ui}/dashboard/login"
    expect(page.get_by_role("button", name="Log out")).to_have_count(0)


def test_logged_out_visit_redirects_to_login(page, ui):  # noqa: F811
    page.goto(f"{ui}/dashboard/payments")
    page.wait_for_url(to_login(ui))
    expect(page.get_by_role("button", name="Log in")).to_be_visible()


def test_log_out_returns_to_login_and_ends_session(page, ui):  # noqa: F811
    _, addr, _ = committed_merchant()
    logged_in(page, ui, addr)
    assert any(c["name"] == "cleartill_session" for c in page.context.cookies())
    page.get_by_role("button", name="Log out").click()
    page.wait_for_url(to_login(ui))
    page.goto(f"{ui}/dashboard/payments")
    page.wait_for_url(to_login(ui))
    expect(page.get_by_role("button", name="Log out")).to_have_count(0)
    with TestClient(app, base_url="https://testserver") as c:
        r = c.get("/dashboard-api/session", headers={"Cookie": cookie_header(page)})
    assert r.status_code == 401, r.text


def cookie_header(page):  # noqa: F811
    return "; ".join(f"{c['name']}={c['value']}" for c in page.context.cookies())


def test_roll_secret_key_shows_new_key_and_revokes_old(page, ui):  # noqa: F811
    _, addr, old_sk = committed_merchant()
    logged_in(page, ui, addr)
    page.get_by_role("link", name="API keys").click()
    page.wait_for_url(f"{ui}/dashboard/keys")
    expect(page.get_by_role("heading", name="API keys")).to_be_visible()
    expect(page.get_by_role("button", name="Roll publishable key")).to_be_visible()
    page.get_by_role("button", name="Roll secret key").click()
    shown = page.get_by_role("status")
    expect(shown).to_contain_text("sk_test_")
    match = re.search(r"sk_test_\w+", shown.inner_text())
    assert match, "new secret key not shown"
    new_sk = match.group(0)
    assert new_sk != old_sk
    with TestClient(app) as c:
        assert c.get("/v1/customers", headers=sk_headers(old_sk)).status_code == 401
        assert c.get("/v1/customers", headers=sk_headers(new_sk)).status_code == 200


def test_capture_request_sends_csrf_and_idempotency_headers(page, ui):  # noqa: F811
    _, addr, sk = committed_merchant()
    pi = manual_capture_pi(sk)
    logged_in(page, ui, addr)
    open_payment(page, ui, pi["id"])
    capture_url = f"{ui}/v1/payment_intents/{pi['id']}/capture"
    with page.expect_request(lambda r: r.url == capture_url and r.method == "POST") as info:
        page.get_by_role("button", name="Capture").click()
    headers = info.value.all_headers()
    assert headers.get("x-cleartill-dashboard") == "1"
    assert headers.get("idempotency-key")
    assert "authorization" not in headers  # cookie-authenticated, no key in the browser
    expect(status(page, "succeeded")).to_be_visible()


# --- idempotency keys (design §5.6): replay on double submit and lost responses --------------


def captured_payment(page, ui):
    _, addr, sk = committed_merchant()
    pi = manual_capture_pi(sk)
    with TestClient(app) as c:
        r = c.post(f"/v1/payment_intents/{pi['id']}/capture", headers=sk_headers(sk))
    assert r.status_code == 200, r.text
    logged_in(page, ui, addr)
    open_payment(page, ui, pi["id"])
    return sk, pi


def refund_posts(page, ui):
    """Record each refund POST's Idempotency-Key and whether its response was a replay."""
    seen = []
    url = f"{ui}/v1/refunds"

    def on_response(response):
        request = response.request
        if request.url == url and request.method == "POST":
            seen.append(
                (
                    request.headers.get("idempotency-key"),
                    response.headers.get("idempotent-replayed") == "true",
                )
            )

    page.on("response", on_response)
    return seen


def test_double_submit_replays_instead_of_refunding_twice(page, ui):
    sk, pi = captured_payment(page, ui)
    seen = refund_posts(page, ui)
    page.get_by_label("Refund amount").fill("500")
    # Two submits in one task, before React can disable the button: only the key protects.
    page.locator("form.refund").evaluate("f => { f.requestSubmit(); f.requestSubmit(); }")
    expect(page.get_by_role("listitem").filter(has_text="Refunded $5.00")).to_have_count(1)
    for _ in range(50):  # let both responses land
        if len(seen) == 2:
            break
        page.wait_for_timeout(100)
    assert len(seen) == 2, seen
    assert seen[0][0] and seen[0][0] == seen[1][0], "both submits must share one key"
    assert sorted(replayed for _, replayed in seen) == [False, True]
    assert len(refunds_for(sk, pi["id"])) == 1
    expect(page.get_by_role("alert")).to_have_count(0)


def test_deliberate_second_refund_uses_a_new_key(page, ui):
    sk, pi = captured_payment(page, ui)
    seen = refund_posts(page, ui)
    for expected in (1, 2):
        page.get_by_label("Refund amount").fill("500")
        page.get_by_role("button", name="Refund").click()
        expect(page.get_by_role("listitem").filter(has_text="Refunded $5.00")).to_have_count(
            expected
        )
    assert len(seen) == 2 and seen[0][0] != seen[1][0]
    assert len(refunds_for(sk, pi["id"])) == 2
    expect(page.get_by_label("Refund amount")).to_have_value("")  # cleared after success


def test_retry_after_lost_response_reuses_the_key(page, ui):
    sk, pi = captured_payment(page, ui)
    url = f"{ui}/v1/refunds"
    keys = []

    def lose_first_response(route):
        keys.append(route.request.headers.get("idempotency-key"))
        if len(keys) == 1:
            route.fetch()  # the server commits the refund...
            route.abort()  # ...but the browser never sees the reply
        else:
            route.continue_()

    page.route(url, lose_first_response)
    seen = refund_posts(page, ui)
    page.get_by_label("Refund amount").fill("500")
    page.get_by_role("button", name="Refund").click()
    expect(page.get_by_role("alert")).to_contain_text("retrying is safe")
    # The lost attempt already committed: the page reloads and shows it.
    expect(page.get_by_role("listitem").filter(has_text="Refunded $5.00")).to_have_count(1)
    with page.expect_response(lambda r: r.url == url and r.request.method == "POST"):
        page.get_by_role("button", name="Refund").click()
    expect(page.get_by_role("alert")).to_have_count(0)
    expect(page.get_by_role("listitem").filter(has_text="Refunded $5.00")).to_have_count(1)
    assert len(keys) == 2 and keys[0] and keys[0] == keys[1]
    # The lost attempt really committed: the retry got the stored response back.
    assert [replayed for _, replayed in seen] == [True]
    assert len(refunds_for(sk, pi["id"])) == 1
