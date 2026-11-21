"""Regression tests for the M8 review (card data in idempotency hashes, Unicode digits, keys)."""

import hashlib
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import cards, clock, network, seed
from app.db import get_db
from app.main import app
from app.models import IdempotencyKey, PaymentMethod

YEAR = datetime.now(UTC).year  # expiry years are relative: no time bomb

FORM = {"Content-Type": "application/x-www-form-urlencoded"}
API_DIR = Path(__file__).resolve().parent.parent


@pytest.fixture
def keys(db):
    merchant, sk, pk = seed.seed(db, "r8@example.com")
    return merchant, {"Authorization": f"Bearer {sk}"}, {"Authorization": f"Bearer {pk}"}


@pytest.fixture
def fixed_clock(monkeypatch):
    monkeypatch.setattr(clock, "now", lambda: datetime(2026, 6, 15, tzinfo=UTC))


def card_body(number="4242424242424242", month="12", year=str(YEAR + 5), cvc="123"):
    return (
        f"type=card&card[number]={number}&card[exp_month]={month}"
        f"&card[exp_year]={year}&card[cvc]={cvc}"
    )


def luhn_complete(prefix: str, length: int) -> str:
    body = prefix + "0" * (length - len(prefix) - 1)
    return next(body + str(d) for d in range(10) if cards.luhn_valid(body + str(d)))


def test_idempotency_hash_is_keyed_and_excludes_the_cvc(client, db, keys, fixed_clock):
    m, _, pk = keys
    h = {**pk, **FORM, "Idempotency-Key": "card-1"}
    first = client.post("/v1/payment_methods", content=card_body(cvc="123"), headers=h)
    # Same card, different CVC: still the same request as far as idempotency is concerned.
    second = client.post("/v1/payment_methods", content=card_body(cvc="456"), headers=h)
    assert first.status_code == second.status_code == 200
    assert second.json()["id"] == first.json()["id"]
    assert second.headers["idempotent-replayed"] == "true"
    row = db.scalar(select(IdempotencyKey).where(IdempotencyKey.merchant_id == m.id))
    assert row.key == "pk:card-1"
    plain = {
        "type": "card",
        "card": {
            "number": "4242424242424242",
            "exp_month": "12",
            "exp_year": str(YEAR + 5),
            "cvc": "123",
        },
    }
    canonical = json.dumps(plain, sort_keys=True, separators=(",", ":"))
    unkeyed = hashlib.sha256(f"POST /v1/payment_methods {canonical}".encode()).hexdigest()
    assert row.request_hash != unkeyed
    assert (
        len(db.scalars(select(PaymentMethod).where(PaymentMethod.merchant_id == m.id)).all()) == 1
    )


def test_publishable_key_cannot_claim_a_secret_key_idempotency_key(client, keys, fixed_clock):
    _, sk, pk = keys
    client.post(
        "/v1/payment_methods",
        content=card_body(),
        headers={**pk, **FORM, "Idempotency-Key": "order-1"},
    )
    r = client.post("/v1/customers", headers={**sk, "Idempotency-Key": "order-1"})
    assert r.status_code == 200 and "idempotent-replayed" not in r.headers


@pytest.mark.parametrize(
    "number",
    [
        "４２４２４２４２４２４２４２４２",  # fullwidth
        "٤٠٠٠٠٠٠٠٠٠٠٠٠٠٠٢",  # Arabic-Indic: the decline test card
    ],
)
def test_unicode_digits_are_not_card_digits(client, keys, number, fixed_clock):
    r = client.post(
        "/v1/payment_methods", content=card_body(number=number), headers={**keys[2], **FORM}
    )
    assert r.status_code == 402 and r.json()["error"]["code"] == "invalid_number"


def test_unicode_digits_in_expiry_and_cvc_are_rejected(client, keys, fixed_clock):
    r = client.post(
        "/v1/payment_methods", content=card_body(cvc="１２３"), headers={**keys[2], **FORM}
    )
    assert r.json()["error"]["code"] == "invalid_cvc"


@pytest.mark.parametrize(("year", "ok"), [("30", True), ("76", True), ("77", False)])
def test_two_digit_years(client, keys, fixed_clock, year, ok):
    r = client.post(
        "/v1/payment_methods", content=card_body(year=year), headers={**keys[2], **FORM}
    )
    assert (r.status_code == 200) is ok
    if ok:
        assert r.json()["card"]["exp_year"] == 2000 + int(year)


@pytest.mark.parametrize(
    ("number", "brand"),
    [
        ("6011111111111117", "discover"),
        (luhn_complete("644", 16), "discover"),
        (luhn_complete("65", 16), "discover"),
        ("2223003122003222", "mastercard"),
        (luhn_complete("9", 16), "unknown"),
    ],
)
def test_more_brands(client, keys, fixed_clock, number, brand):
    r = client.post(
        "/v1/payment_methods", content=card_body(number=number), headers={**keys[2], **FORM}
    )
    assert r.status_code == 200 and r.json()["card"]["brand"] == brand


def test_dashboard_cookie_cannot_tokenize(db, fixed_clock):
    app.dependency_overrides[get_db] = lambda: (yield db)
    try:
        c = TestClient(app, base_url="https://testserver")
        c.post(
            "/dashboard-api/signup",
            data={"email": "ck8@example.com", "password": "correct horse"},
            headers={"X-Cleartill-Dashboard": "1"},
        )
        r = c.post(
            "/v1/payment_methods",
            content=card_body(),
            headers={**FORM, "X-Cleartill-Dashboard": "1"},
        )
        assert r.status_code == 403
    finally:
        app.dependency_overrides.clear()


def test_unknown_simulated_outcome_raises():
    with pytest.raises(ValueError):
        network.authorize("succes", 100)


@pytest.mark.parametrize("secret", ["", "short-secret"])
def test_app_refuses_to_start_without_a_strong_secret_key(secret):
    env = {**os.environ, "SECRET_KEY": secret}
    proc = subprocess.run(
        [sys.executable, "-c", "import app.main"],
        cwd=API_DIR,
        env=env,
        capture_output=True,
        text=True,
    )
    assert proc.returncode != 0 and "SECRET_KEY" in proc.stderr
