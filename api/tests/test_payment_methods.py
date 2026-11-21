import re
from datetime import UTC, datetime
from urllib.parse import urlencode

import pytest

from app import clock, seed

YEAR = datetime.now(UTC).year  # expiry years are relative: no time bomb

FORM = {"Content-Type": "application/x-www-form-urlencoded"}
PM_ID = re.compile(r"^pm_[0-9A-Za-z]+$")
KEYS = {"id", "object", "type", "card", "created", "livemode"}
CARD_KEYS = {"brand", "last4", "exp_month", "exp_year", "fingerprint"}
TEST_CARDS = [
    "4242424242424242",
    "4000000000000002",
    "4000000000009995",
    "4000000000000069",
    "4000000000000127",
    "4000000000000119",
    "4000002760003184",
]
FIXED_NOW = datetime(2026, 6, 15, 12, 0, tzinfo=UTC)


@pytest.fixture
def keys(db):
    return seed.seed(db, "a@example.com")


@pytest.fixture
def pkh(keys):
    return {"Authorization": f"Bearer {keys[2]}", **FORM}


@pytest.fixture
def skh(keys):
    return {"Authorization": f"Bearer {keys[1]}", **FORM}


@pytest.fixture
def fixed_clock(monkeypatch):
    monkeypatch.setattr(clock, "now", lambda: FIXED_NOW)


def err(r):
    return r.json()["error"]


def body(number="4242424242424242", exp_month="12", exp_year=str(YEAR + 5), cvc="123", **extra):
    params = {"type": "card"}
    if number is not None:
        params["card[number]"] = number
    params.update({"card[exp_month]": exp_month, "card[exp_year]": exp_year, "card[cvc]": cvc})
    params.update(extra)
    return urlencode(params)


def tokenize(client, h, **kw):
    return client.post("/v1/payment_methods", content=body(**kw), headers=h)


def create(client, h, **kw):
    r = tokenize(client, h, **kw)
    assert r.status_code == 200, r.text
    return r.json()


# --- success -------------------------------------------------------------------------------


@pytest.mark.parametrize("number", TEST_CARDS)
def test_tokenize_test_card(client, pkh, number):
    pm = create(client, pkh, number=number, exp_month="7", exp_year=str(YEAR + 6))
    assert set(pm) == KEYS
    assert set(pm["card"]) == CARD_KEYS
    assert PM_ID.match(pm["id"])
    assert pm["object"] == "payment_method"
    assert pm["type"] == "card"
    assert pm["livemode"] is False
    assert isinstance(pm["created"], int) and not isinstance(pm["created"], bool)
    assert pm["card"]["brand"] == "visa"
    assert pm["card"]["last4"] == number[-4:]
    assert pm["card"]["exp_month"] == 7
    assert pm["card"]["exp_year"] == YEAR + 6


def test_mastercard_brand(client, pkh):
    pm = create(client, pkh, number="5555555555554444")
    assert pm["card"]["brand"] == "mastercard"
    assert pm["card"]["last4"] == "4444"


def test_amex_brand_with_four_digit_cvc(client, pkh):
    pm = create(client, pkh, number="378282246310005", cvc="1234")
    assert pm["card"]["brand"] == "amex"
    assert pm["card"]["last4"] == "0005"


def test_same_number_same_fingerprint(client, pkh):
    a = create(client, pkh)
    b = create(client, pkh, exp_month="1", exp_year=str(YEAR + 7), cvc="999")
    assert a["id"] != b["id"]
    assert a["card"]["fingerprint"] == b["card"]["fingerprint"]


def test_different_number_different_fingerprint(client, pkh):
    a = create(client, pkh)
    b = create(client, pkh, number="4000000000000002")
    assert a["card"]["fingerprint"] != b["card"]["fingerprint"]


def test_number_with_spaces_is_accepted_with_same_fingerprint(client, pkh):
    plain = create(client, pkh)
    spaced = create(client, pkh, number="4242 4242 4242 4242")
    assert spaced["card"]["last4"] == "4242"
    assert spaced["card"]["fingerprint"] == plain["card"]["fingerprint"]


def test_number_with_dashes_is_accepted_with_same_fingerprint(client, pkh):
    plain = create(client, pkh)
    dashed = create(client, pkh, number="4242-4242-4242-4242")
    assert dashed["card"]["fingerprint"] == plain["card"]["fingerprint"]


# --- card errors ---------------------------------------------------------------------------


def assert_card_error(r, code, param):
    assert r.status_code == 402, r.text
    e = err(r)
    assert e["type"] == "card_error"
    assert e["code"] == code
    assert e["param"] == param


@pytest.mark.parametrize(
    "number", ["42424242424", "42424242424242424242", "4242abcd42424242", "", "4" * 25]
)
def test_invalid_number_length_or_chars(client, pkh, number):
    assert_card_error(tokenize(client, pkh, number=number), "invalid_number", "card[number]")


def test_luhn_failure_is_incorrect_number(client, pkh):
    r = tokenize(client, pkh, number="4242424242424241")
    assert_card_error(r, "incorrect_number", "card[number]")


@pytest.mark.parametrize("month", ["0", "13", "-1", "ab"])
def test_invalid_expiry_month(client, pkh, fixed_clock, month):
    assert_card_error(
        tokenize(client, pkh, exp_month=month), "invalid_expiry_month", "card[exp_month]"
    )


def test_expiry_year_in_past(client, pkh, fixed_clock):
    r = tokenize(client, pkh, exp_year="2025")
    assert_card_error(r, "invalid_expiry_year", "card[exp_year]")


def test_expiry_year_more_than_50_years_ahead(client, pkh, fixed_clock):
    r = tokenize(client, pkh, exp_year="2077")
    assert_card_error(r, "invalid_expiry_year", "card[exp_year]")


def test_expiry_year_50_years_ahead_is_accepted(client, pkh, fixed_clock):
    assert create(client, pkh, exp_year="2076")["card"]["exp_year"] == 2076


def test_expiry_month_past_in_current_year(client, pkh, fixed_clock):
    r = tokenize(client, pkh, exp_month="5", exp_year="2026")
    assert_card_error(r, "invalid_expiry_year", "card[exp_year]")


def test_expiry_current_month_is_accepted(client, pkh, fixed_clock):
    pm = create(client, pkh, exp_month="6", exp_year="2026")
    assert (pm["card"]["exp_month"], pm["card"]["exp_year"]) == (6, 2026)


@pytest.mark.parametrize("cvc", ["12", "1234", "abc", ""])
def test_invalid_cvc_for_visa(client, pkh, cvc):
    assert_card_error(tokenize(client, pkh, cvc=cvc), "invalid_cvc", "card[cvc]")


def test_three_digit_cvc_invalid_for_amex(client, pkh):
    r = tokenize(client, pkh, number="378282246310005", cvc="123")
    assert_card_error(r, "invalid_cvc", "card[cvc]")


# --- keys and params -----------------------------------------------------------------------


def test_create_with_secret_key_is_forbidden(client, skh):
    r = tokenize(client, skh)
    assert r.status_code == 403
    assert err(r)["code"] == "permission_denied"


def test_retrieve_with_publishable_key_is_forbidden(client, pkh, skh):
    pm = create(client, pkh)
    r = client.get(f"/v1/payment_methods/{pm['id']}", headers=pkh)
    assert r.status_code == 403
    assert err(r)["code"] == "permission_denied"


def test_unknown_param_is_parameter_unknown(client, pkh):
    r = tokenize(client, pkh, foo="1")
    assert r.status_code == 400
    assert err(r)["code"] == "parameter_unknown"


def test_missing_number_is_parameter_missing(client, pkh):
    r = tokenize(client, pkh, number=None)
    assert r.status_code == 400
    assert err(r)["code"] == "parameter_missing"
    assert err(r)["param"] == "card[number]"


# --- retrieve ------------------------------------------------------------------------------


def test_retrieve_equals_create(client, pkh, skh):
    pm = create(client, pkh)
    r = client.get(f"/v1/payment_methods/{pm['id']}", headers=skh)
    assert r.status_code == 200, r.text
    assert r.json() == pm


def test_retrieve_other_merchant_is_404(client, db, pkh):
    pm = create(client, pkh)
    _, other_sk, _ = seed.seed(db, "b@example.com")
    r = client.get(
        f"/v1/payment_methods/{pm['id']}", headers={"Authorization": f"Bearer {other_sk}"}
    )
    assert r.status_code == 404
    assert err(r)["code"] == "resource_missing"


def test_retrieve_unknown_id_is_404(client, skh):
    r = client.get("/v1/payment_methods/pm_doesnotexist", headers=skh)
    assert r.status_code == 404
    assert err(r)["code"] == "resource_missing"
