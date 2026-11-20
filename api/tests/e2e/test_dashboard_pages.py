# ruff: noqa: F811  (pytest fixtures shared from test_checkout_ui / test_dashboard_ui)
"""M20: dashboard Customers, Balance and Webhooks pages in a real browser against a live
server (committed data; rows stay behind). The live server runs in-process, so env changes
made with monkeypatch (ALLOW_INSECURE_URLS) apply to it."""

import re
from urllib.parse import urlencode

import pytest
from fastapi.testclient import TestClient
from playwright.sync_api import expect

# Fixtures (and the module-level skip when the SPA isn't built) come from the checkout tests.
from test_checkout_ui import base_url, browser, page  # noqa: F401
from test_dashboard_ui import _reset_ratelimit, committed_merchant, logged_in, sk_headers, ui  # noqa: F401
from test_webhook_worker import Receiver

from app.main import app


@pytest.fixture
def receiver():
    rx = Receiver([200])
    yield rx
    rx.close()


@pytest.fixture
def insecure(monkeypatch):
    monkeypatch.setenv("ALLOW_INSECURE_URLS", "1")


def sk_post(sk, path, body):
    with TestClient(app) as c:
        r = c.post(path, content=urlencode(body), headers=sk_headers(sk))
    assert r.status_code == 200, r.text
    return r.json()


def test_customers_and_balance_pages_render_seeded_data(page, ui):
    _, addr, sk = committed_merchant()
    sk_post(sk, "/v1/customers", {"email": "seed@example.com"})
    pi = sk_post(
        sk,
        "/v1/payment_intents",
        {"amount": "2000", "currency": "usd", "payment_method": "pm_card_visa", "confirm": "true"},
    )
    assert pi["status"] == "succeeded", pi
    logged_in(page, ui, addr)

    page.get_by_role("link", name="Customers").click()
    page.wait_for_url(f"{ui}/dashboard/customers")
    expect(page.get_by_role("heading", name="Customers", exact=True)).to_be_visible()
    expect(page.get_by_role("row").filter(has_text="seed@example.com")).to_be_visible()

    page.get_by_role("link", name="Balance").click()
    page.wait_for_url(f"{ui}/dashboard/balance")
    expect(page.get_by_role("heading", name="Balance", exact=True)).to_be_visible()
    expect(page.get_by_text("Pending").first).to_be_visible()
    expect(page.locator("body")).to_contain_text(re.compile(r"Available[\s\S]*\$\d"))
    expect(page.locator("body")).to_contain_text(re.compile(r"Pending[\s\S]*\$\d"))
    expect(page.get_by_role("row").filter(has_text="charge").first).to_be_visible()


def test_add_endpoint_shows_secret_and_lists_url(page, ui, insecure, receiver):
    _, addr, _ = committed_merchant()
    url = f"http://127.0.0.1:{receiver.port}/hook"
    logged_in(page, ui, addr)
    page.get_by_role("link", name="Webhooks").click()
    page.wait_for_url(f"{ui}/dashboard/webhooks")
    expect(page.get_by_role("heading", name="Webhooks", exact=True)).to_be_visible()
    expect(page.get_by_role("link", name=url)).to_have_count(0)

    page.get_by_label("Endpoint URL").fill(url)
    page.get_by_role("button", name="Add endpoint").click()
    expect(page.get_by_role("status")).to_contain_text("whsec_")
    expect(page.get_by_role("link", name=url)).to_be_visible()


def test_resend_from_endpoint_page(page, ui, insecure, receiver):
    _, addr, sk = committed_merchant()
    url = f"http://127.0.0.1:{receiver.port}/hook"
    ep = sk_post(sk, "/v1/webhook_endpoints", {"url": url, "enabled_events[]": "*"})
    sk_post(sk, "/v1/customers", {})  # one pending delivery; the worker isn't run

    logged_in(page, ui, addr)
    page.get_by_role("link", name="Webhooks").click()
    page.get_by_role("link", name=url).click()
    page.wait_for_url(f"{ui}/dashboard/webhooks/{ep['id']}")
    expect(page.get_by_role("heading", name="Webhook endpoint")).to_be_visible()
    row = page.get_by_role("row").filter(has_text="customer.created")
    expect(row).to_have_count(1)

    row.get_by_role("button", name="Resend").click()
    expect(page.get_by_role("status")).to_contain_text("Resent: 200")
    expect(row).to_contain_text("succeeded")
    assert len(receiver.requests) == 1
    assert receiver.requests[0]["path"] == "/hook"

    row.get_by_role("button", name="Attempts").click()
    expect(page.get_by_role("listitem").filter(has_text="manual")).to_have_count(1)
