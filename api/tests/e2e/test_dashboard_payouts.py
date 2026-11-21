# ruff: noqa: F811  (pytest fixtures shared from test_checkout_ui / test_dashboard_ui)
"""M23: paying out from the dashboard Balance page in a real browser against a live server
(committed data; rows stay behind). The live server runs in-process, so the monkeypatched
AVAILABILITY_DELAY_SECONDS applies to it."""

import re

import pytest
from playwright.sync_api import expect

# Fixtures (and the module-level skip when the SPA isn't built) come from the checkout tests.
from test_checkout_ui import base_url, browser, page  # noqa: F401
from test_dashboard_pages import sk_post
from test_dashboard_ui import _reset_ratelimit, committed_merchant, logged_in, ui  # noqa: F401

from app import housekeeping
from app.db import SessionLocal

PAID = {"amount": "2000", "currency": "usd", "payment_method": "pm_card_visa", "confirm": "true"}


@pytest.fixture
def funded(monkeypatch):
    """A committed merchant with $19.12 available (2000 - 88 fee)."""
    monkeypatch.setenv("AVAILABILITY_DELAY_SECONDS", "0")
    _, addr, sk = committed_merchant()
    assert sk_post(sk, "/v1/payment_intents", PAID)["status"] == "succeeded"
    with SessionLocal.begin() as s:
        housekeeping.release_due_funds(s)
    return addr


def available(page):
    return page.locator("dt", has_text="Available").locator("xpath=following-sibling::dd[1]")


def open_balance(page, ui, addr):
    logged_in(page, ui, addr)
    page.get_by_role("link", name="Balance").click()
    page.wait_for_url(f"{ui}/dashboard/balance")
    expect(available(page)).to_have_text("$19.12")


def test_pay_out_from_balance_page(page, ui, funded):
    open_balance(page, ui, funded)
    page.get_by_label("Payout amount (cents)").fill("500")
    page.get_by_role("button", name="Pay out").click()
    row = page.get_by_role("row").filter(has_text="$5.00").filter(has_text="pending")
    expect(row.first).to_be_visible()
    expect(available(page)).to_have_text("$14.12")


def test_pay_out_over_balance_shows_alert(page, ui, funded):
    open_balance(page, ui, funded)
    page.get_by_label("Payout amount (cents)").fill("100000")
    page.get_by_role("button", name="Pay out").click()
    expect(page.get_by_role("alert")).to_contain_text(re.compile("balance", re.I))
    expect(available(page)).to_have_text("$19.12")
