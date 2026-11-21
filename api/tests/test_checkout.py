"""M17: hosted checkout sessions (create/list/retrieve, public view, pay, 3DS, expiry, worker)."""

import re
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest
from sqlalchemy import text

from app import checkout as checkout_mod
from app import clock, housekeeping, ledger, seed
from app.ledger_check import ledger_check

FORM = {"Content-Type": "application/x-www-form-urlencoded"}
EXP_YEAR = str(datetime.now(UTC).year + 5)
SUCCESS_URL = "https://example.com/ok"
CANCEL_URL = "https://example.com/back"
VISA = "4242424242424242"
DECLINE = "4000000000000002"
THREE_DS = "4000002760003184"
PAST_EXPIRY = timedelta(hours=24, seconds=1)
SESSION_KEYS = {
    "id",
    "object",
    "mode",
    "amount_total",
    "currency",
    "status",
    "payment_status",
    "payment_intent",
    "success_url",
    "cancel_url",
    "url",
    "customer",
    "client_reference_id",
    "metadata",
    "expires_at",
    "created",
    "livemode",
}
PUBLIC_KEYS = {
    "id",
    "status",
    "amount_total",
    "currency",
    "line_items",
    "success_url",
    "cancel_url",
    "last_payment_error",
}


@pytest.fixture(autouse=True)
def ledger_clean(db):
    yield
    assert ledger_check(db, own_txn=False) == []


@pytest.fixture
def keys(db):
    return seed.seed(db, "checkout-a@example.com")


@pytest.fixture
def skh(keys):
    return {"Authorization": f"Bearer {keys[1]}", **FORM}


@pytest.fixture
def other_skh(db):
    return {"Authorization": f"Bearer {seed.seed(db, 'checkout-b@example.com')[1]}", **FORM}


def err(r):
    return r.json()["error"]


def post(client, h, path, data=None):
    return client.post(path, content=urlencode(data or {}), headers=h)


def session_body(**over):
    data = {
        "mode": "payment",
        "line_items[0][price_data][currency]": "usd",
        "line_items[0][price_data][unit_amount]": "1500",
        "line_items[0][price_data][product_data][name]": "Tee",
        "line_items[0][quantity]": "2",
        "success_url": SUCCESS_URL,
        "cancel_url": CANCEL_URL,
    }
    data.update(over)
    return {k: v for k, v in data.items() if v is not None}


def create_raw(client, h, data):
    return post(client, h, "/v1/checkout/sessions", data)


def create_session(client, h, **over):
    r = create_raw(client, h, session_body(**over))
    assert r.status_code == 200, r.text
    return r.json()


def get_session(client, h, cs_id):
    r = client.get(f"/v1/checkout/sessions/{cs_id}", headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def retrieve_pi(client, h, pid):
    r = client.get(f"/v1/payment_intents/{pid}", headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def public(client, cs_id):
    return client.get(f"/checkout-api/sessions/{cs_id}")


def card(number=VISA, **over):
    data = {
        "card[number]": number,
        "card[exp_month]": "12",
        "card[exp_year]": EXP_YEAR,
        "card[cvc]": "123",
    }
    data.update(over)
    return {k: v for k, v in data.items() if v is not None}


def pay(client, cs_id, number=VISA, **over):
    """The hosted page posts the card with no Authorization header."""
    return post(client, FORM, f"/checkout-api/sessions/{cs_id}/pay", card(number, **over))


def challenge(client, pid, **data):
    return post(client, FORM, f"/checkout-api/3ds/{pid}", data)


def charges_for(client, h, pid):
    r = client.get("/v1/charges", params={"payment_intent": pid}, headers=h)
    assert r.status_code == 200, r.text
    return r.json()["data"]


def events(client, h, type=None):
    params = {"limit": 100} | ({"type": type} if type else {})
    r = client.get("/v1/events", params=params, headers=h)
    assert r.status_code == 200, r.text
    return r.json()["data"]


def event_types(client, h):
    return [e["type"] for e in events(client, h)]  # newest first


def assert_400(r, code=None, param=None):
    assert r.status_code == 400, r.text
    e = err(r)
    assert e["type"] == "invalid_request_error"
    if code is not None:
        assert e.get("code") == code
    if param is not None:
        assert e.get("param") == param


def assert_404(r):
    assert r.status_code == 404, r.text
    assert err(r).get("code") == "resource_missing"


def later(monkeypatch, delta=PAST_EXPIRY):
    when = clock.now() + delta
    monkeypatch.setattr(clock, "now", lambda: when)


def three_ds_paid(client, skh):
    """A session whose PI is in requires_action: returns (session, client_secret)."""
    cs = create_session(client, skh)
    r = pay(client, cs["id"], THREE_DS)
    assert r.status_code == 200, r.text
    url = r.json()["redirect"]
    prefix = f"https://testserver/checkout/3ds/{cs['payment_intent']}?client_secret="
    assert url.startswith(prefix), url
    return cs, parse_qs(urlsplit(url).query)["client_secret"][0]


# --- create ---------------------------------------------------------------------------------


def test_create_returns_checkout_session(client, skh):
    cs = create_session(client, skh)
    assert set(cs) == SESSION_KEYS
    assert re.fullmatch(r"cs_\w+", cs["id"])
    assert cs["object"] == "checkout.session"
    assert cs["mode"] == "payment"
    assert cs["amount_total"] == 3000
    assert cs["currency"] == "usd"
    assert cs["status"] == "open"
    assert cs["payment_status"] == "unpaid"
    assert re.fullmatch(r"pi_\w+", cs["payment_intent"])
    assert cs["success_url"] == SUCCESS_URL
    assert cs["cancel_url"] == CANCEL_URL
    assert cs["url"] == f"https://testserver/checkout/{cs['id']}"
    assert cs["customer"] is None
    assert cs["client_reference_id"] is None
    assert cs["metadata"] == {}
    assert cs["livemode"] is False


def test_create_expires_after_24h(client, skh):
    cs = create_session(client, skh)
    assert isinstance(cs["expires_at"], int)
    assert cs["expires_at"] - cs["created"] == 24 * 3600


def test_create_with_template_success_url(client, skh):
    url = "https://example.com/ok?cs={CHECKOUT_SESSION_ID}"
    r = create_raw(client, skh, session_body(success_url=url))
    assert r.status_code == 200, r.text


def test_create_with_optional_fields(client, skh):
    cus = post(client, skh, "/v1/customers", {"email": "c@example.com"})
    assert cus.status_code == 200, cus.text
    cs = create_session(
        client,
        skh,
        customer=cus.json()["id"],
        client_reference_id="order-7",
        **{"metadata[order]": "7"},
    )
    assert cs["customer"] == cus.json()["id"]
    assert cs["client_reference_id"] == "order-7"
    assert cs["metadata"] == {"order": "7"}


def test_create_with_other_merchants_customer_is_404(client, skh, other_skh):
    cus = post(client, other_skh, "/v1/customers", {"email": "c@example.com"})
    assert cus.status_code == 200, cus.text
    assert_404(create_raw(client, skh, session_body(customer=cus.json()["id"])))


def test_create_makes_unconfirmed_automatic_pi(client, skh):
    cs = create_session(client, skh)
    pi = retrieve_pi(client, skh, cs["payment_intent"])
    assert pi["amount"] == 3000
    assert pi["currency"] == "usd"
    assert pi["status"] == "requires_payment_method"
    assert pi["capture_method"] == "automatic"
    assert pi["return_url"] == f"https://testserver/checkout/{cs['id']}"


def test_create_emits_payment_intent_created(client, skh):
    cs = create_session(client, skh)
    (e,) = events(client, skh, "payment_intent.created")
    assert e["data"]["object"]["id"] == cs["payment_intent"]


# --- create validation ----------------------------------------------------------------------


@pytest.mark.parametrize("mode", [None, "subscription"])
def test_create_bad_mode_is_400(client, skh, mode):
    assert_400(create_raw(client, skh, session_body(mode=mode)), param="mode")


def test_create_without_line_items_is_400(client, skh):
    data = {k: v for k, v in session_body().items() if not k.startswith("line_items")}
    assert_400(create_raw(client, skh, data), param="line_items")


def test_create_zero_quantity_is_400(client, skh):
    assert_400(create_raw(client, skh, session_body(**{"line_items[0][quantity]": "0"})))


def test_create_unsupported_currency_is_400(client, skh):
    body = session_body(**{"line_items[0][price_data][currency]": "eur"})
    assert_400(create_raw(client, skh, body))


@pytest.mark.parametrize(("unit", "qty"), [("10", "1"), ("50000000", "2")])
def test_create_total_out_of_bounds_is_400(client, skh, unit, qty):
    body = session_body(
        **{"line_items[0][price_data][unit_amount]": unit, "line_items[0][quantity]": qty}
    )
    assert_400(create_raw(client, skh, body), param="line_items")


def test_create_without_success_url_is_400(client, skh):
    assert_400(create_raw(client, skh, session_body(success_url=None)), param="success_url")


@pytest.mark.parametrize("url", ["javascript:alert(1)", "data:text/html,x"])
def test_create_bad_success_url_is_400(client, skh, url):
    r = create_raw(client, skh, session_body(success_url=url))
    assert_400(r, "url_invalid", "success_url")


def test_create_bad_cancel_url_is_400(client, skh):
    r = create_raw(client, skh, session_body(cancel_url="javascript:alert(1)"))
    assert_400(r, param="cancel_url")


def test_create_needs_secret_key(client, keys):
    pkh = {"Authorization": f"Bearer {keys[2]}", **FORM}
    assert create_raw(client, pkh, session_body()).status_code == 403


# --- list / retrieve ------------------------------------------------------------------------


def test_retrieve_and_list(client, skh):
    cs = create_session(client, skh)
    assert get_session(client, skh, cs["id"]) == cs
    r = client.get("/v1/checkout/sessions", headers=skh)
    assert r.status_code == 200, r.text
    assert [s["id"] for s in r.json()["data"]] == [cs["id"]]


def test_other_merchants_session_is_404_and_not_listed(client, skh, other_skh):
    cs = create_session(client, other_skh)
    assert_404(client.get(f"/v1/checkout/sessions/{cs['id']}", headers=skh))
    r = client.get("/v1/checkout/sessions", headers=skh)
    assert r.status_code == 200, r.text
    assert r.json()["data"] == []


# --- checkout PIs can't be driven through /v1 -----------------------------------------------


@pytest.mark.parametrize(
    ("suffix", "data"),
    [("", {"metadata[k]": "x"}), ("/confirm", {"payment_method": "pm_card_visa"}), ("/cancel", {})],
)
def test_checkout_pi_mutations_are_blocked(client, skh, suffix, data):
    cs = create_session(client, skh)
    pid = cs["payment_intent"]
    before = retrieve_pi(client, skh, pid)
    r = post(client, skh, f"/v1/payment_intents/{pid}{suffix}", data)
    assert_400(r, "payment_intent_unexpected_state")
    assert retrieve_pi(client, skh, pid) == before
    assert charges_for(client, skh, pid) == []


# --- public view ----------------------------------------------------------------------------


def test_public_view_shape(client, skh):
    cs = create_session(client, skh)
    r = public(client, cs["id"])
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) == PUBLIC_KEYS
    assert body["id"] == cs["id"]
    assert body["status"] == "open"
    assert body["amount_total"] == 3000
    assert body["currency"] == "usd"
    assert body["line_items"] == [
        {"name": "Tee", "quantity": 2, "unit_amount": 1500, "amount_total": 3000}
    ]
    assert body["success_url"] == SUCCESS_URL
    assert body["cancel_url"] == CANCEL_URL
    assert body["last_payment_error"] is None


def test_public_view_leaks_no_secrets(client, keys, skh):
    cs = create_session(client, skh)
    secret = retrieve_pi(client, skh, cs["payment_intent"])["client_secret"]
    r = public(client, cs["id"])
    assert r.status_code == 200, r.text
    assert "client_secret" not in r.text
    assert secret not in r.text
    assert "payment_intent" not in r.json()
    assert keys[1] not in r.text and keys[2] not in r.text


def test_public_view_unknown_is_404(client):
    assert_404(public(client, "cs_doesnotexist0000"))


def test_public_view_shows_expired_after_expires_at(client, skh, monkeypatch):
    cs = create_session(client, skh)
    later(monkeypatch)
    r = public(client, cs["id"])
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "expired"


# --- pay: success ---------------------------------------------------------------------------


def test_pay_redirects_to_success_url(client, skh):
    cs = create_session(client, skh)
    r = pay(client, cs["id"])
    assert r.status_code == 200, r.text
    assert r.json() == {"redirect": SUCCESS_URL}


def test_pay_replaces_session_id_template(client, skh):
    cs = create_session(client, skh, success_url="https://example.com/ok?cs={CHECKOUT_SESSION_ID}")
    r = pay(client, cs["id"])
    assert r.status_code == 200, r.text
    assert set(r.json()) == {"redirect"}
    u = urlsplit(r.json()["redirect"])
    assert (u.scheme, u.netloc, u.path) == ("https", "example.com", "/ok")
    assert parse_qs(u.query) == {"cs": [cs["id"]]}


def test_pay_completes_session(client, skh):
    cs = create_session(client, skh)
    assert pay(client, cs["id"]).status_code == 200
    got = get_session(client, skh, cs["id"])
    assert got["status"] == "complete"
    assert got["payment_status"] == "paid"
    assert got["url"] is None


def test_pay_succeeds_pi_with_one_captured_charge(client, skh):
    cs = create_session(client, skh)
    assert pay(client, cs["id"]).status_code == 200
    assert retrieve_pi(client, skh, cs["payment_intent"])["status"] == "succeeded"
    (ch,) = charges_for(client, skh, cs["payment_intent"])
    assert ch["status"] == "succeeded"
    assert ch["captured"] is True
    assert ch["amount"] == 3000


def test_pay_emits_completed_after_pi_succeeded(client, skh):
    cs = create_session(client, skh)
    assert pay(client, cs["id"]).status_code == 200
    (e,) = events(client, skh, "checkout.session.completed")
    assert e["data"]["object"]["id"] == cs["id"]
    types = event_types(client, skh)
    assert types.index("checkout.session.completed") < types.index("payment_intent.succeeded")


def test_pay_posts_pending_balance(client, db, keys, skh):
    cs = create_session(client, skh)
    assert pay(client, cs["id"]).status_code == 200
    assert ledger.balance(db, keys[0].id)["pending"] == [
        {"amount": 3000 - ledger.fee(3000), "currency": "usd"}
    ]


def test_pay_stores_no_card_number(client, db, keys, skh):
    cs = create_session(client, skh)
    assert pay(client, cs["id"]).status_code == 200
    n = db.scalar(
        text("SELECT count(*) FROM payment_methods WHERE merchant_id = :m AND last4 = '4242'"),
        {"m": keys[0].id},
    )
    assert n == 1
    blob = db.scalar(
        text("SELECT coalesce(string_agg(data::text, ''), '') FROM events WHERE merchant_id = :m"),
        {"m": keys[0].id},
    )
    assert VISA not in blob


# --- pay: decline and retry -----------------------------------------------------------------


def test_pay_decline_is_402_without_secrets(client, skh):
    cs = create_session(client, skh)
    r = pay(client, cs["id"], DECLINE)
    assert r.status_code == 402, r.text
    e = err(r)
    assert e["type"] == "card_error"
    assert e["code"] == "card_declined"
    assert e["message"]
    assert "client_secret" not in r.text
    assert "payment_intent" not in r.text


def test_pay_decline_leaves_session_open_with_error(client, skh):
    cs = create_session(client, skh)
    assert pay(client, cs["id"], DECLINE).status_code == 402
    assert get_session(client, skh, cs["id"])["status"] == "open"
    view = public(client, cs["id"]).json()
    assert view["status"] == "open"
    assert view["last_payment_error"]["message"]


def test_retry_after_decline_succeeds_on_same_pi(client, skh):
    cs = create_session(client, skh)
    assert pay(client, cs["id"], DECLINE).status_code == 402
    r = pay(client, cs["id"])
    assert r.status_code == 200, r.text
    got = get_session(client, skh, cs["id"])
    assert got["payment_intent"] == cs["payment_intent"]
    assert got["status"] == "complete"
    statuses = sorted(c["status"] for c in charges_for(client, skh, cs["payment_intent"]))
    assert statuses == ["failed", "succeeded"]


# --- pay: 3DS -------------------------------------------------------------------------------


def test_pay_3ds_redirects_to_challenge(client, skh):
    cs, _ = three_ds_paid(client, skh)
    assert retrieve_pi(client, skh, cs["payment_intent"])["status"] == "requires_action"
    assert get_session(client, skh, cs["id"])["status"] == "open"


def test_3ds_approve_completes_session(client, skh):
    cs, secret = three_ds_paid(client, skh)
    pid = cs["payment_intent"]
    r = challenge(client, pid, client_secret=secret, result="approve")
    assert r.status_code == 200, r.text
    # Approved and paid: straight to the merchant (user-approved change after M18 review #4).
    success = cs["success_url"].replace("{CHECKOUT_SESSION_ID}", cs["id"])
    assert r.json() == {"redirect": success}
    got = get_session(client, skh, cs["id"])
    assert (got["status"], got["payment_status"]) == ("complete", "paid")
    (e,) = events(client, skh, "checkout.session.completed")
    assert e["data"]["object"]["id"] == cs["id"]


def test_3ds_fail_leaves_session_open_with_error(client, skh):
    cs, secret = three_ds_paid(client, skh)
    r = challenge(client, cs["payment_intent"], client_secret=secret, result="fail")
    assert r.status_code == 200, r.text
    assert get_session(client, skh, cs["id"])["status"] == "open"
    view = public(client, cs["id"]).json()
    assert view["status"] == "open"
    assert view["last_payment_error"]["message"]
    assert events(client, skh, "checkout.session.completed") == []


# --- pay: not payable -----------------------------------------------------------------------


def test_pay_complete_session_is_unexpected_state(client, skh):
    cs = create_session(client, skh)
    assert pay(client, cs["id"]).status_code == 200
    assert_400(pay(client, cs["id"]), "checkout_session_unexpected_state")
    assert len(charges_for(client, skh, cs["payment_intent"])) == 1


def test_pay_expired_session_is_unexpected_state(client, skh, monkeypatch):
    cs = create_session(client, skh)
    later(monkeypatch)
    assert_400(pay(client, cs["id"]), "checkout_session_unexpected_state")
    assert charges_for(client, skh, cs["payment_intent"]) == []


def test_pay_unknown_session_is_404(client):
    assert_404(pay(client, "cs_doesnotexist0000"))


# --- pay: invalid input ---------------------------------------------------------------------


def test_pay_bad_luhn_is_402_without_echo(client, skh):
    cs = create_session(client, skh)
    bad = "4242424242424241"
    r = pay(client, cs["id"], bad, **{"card[cvc]": "987"})
    assert r.status_code == 402, r.text
    e = err(r)
    assert e["code"] == "incorrect_number"
    assert bad not in r.text
    assert "4241" not in e["message"]
    assert "987" not in e["message"]
    assert charges_for(client, skh, cs["payment_intent"]) == []


@pytest.mark.parametrize("field", ["card[number]", "card[exp_month]", "card[exp_year]"])
def test_pay_missing_card_field_is_400(client, skh, field):
    cs = create_session(client, skh)
    r = pay(client, cs["id"], **{field: None})
    assert_400(r)
    assert VISA not in r.text


def test_pay_unknown_field_is_400(client, skh):
    cs = create_session(client, skh)
    r = pay(client, cs["id"], amount="1")
    assert_400(r, "parameter_unknown")
    assert charges_for(client, skh, cs["payment_intent"]) == []
    assert get_session(client, skh, cs["id"])["status"] == "open"


# --- expiry ---------------------------------------------------------------------------------


def expire(db):
    from app import checkout  # M17 module; imported here so the rest of the file still runs

    return checkout.expire_sessions(db)


def test_expire_open_session_past_expires_at(client, db, skh, monkeypatch):
    cs = create_session(client, skh)
    later(monkeypatch)
    assert expire(db) == 1
    got = get_session(client, skh, cs["id"])
    assert got["status"] == "expired"
    assert got["url"] is None
    pi = retrieve_pi(client, skh, cs["payment_intent"])
    assert pi["status"] == "canceled"
    assert pi["cancellation_reason"] == "abandoned"


def test_expire_emits_expired_then_canceled(client, db, skh, monkeypatch):
    cs = create_session(client, skh)
    later(monkeypatch)
    assert expire(db) == 1
    (e,) = events(client, skh, "checkout.session.expired")
    assert e["data"]["object"]["id"] == cs["id"]
    assert len(events(client, skh, "payment_intent.canceled")) == 1
    types = event_types(client, skh)  # newest first
    assert types.index("payment_intent.canceled") < types.index("checkout.session.expired")


def test_expire_cancels_pi_stuck_in_requires_action(client, db, skh, monkeypatch):
    cs, _ = three_ds_paid(client, skh)
    later(monkeypatch)
    assert expire(db) == 1
    assert get_session(client, skh, cs["id"])["status"] == "expired"
    assert retrieve_pi(client, skh, cs["payment_intent"])["status"] == "canceled"


def test_expire_leaves_complete_session_alone(client, db, skh, monkeypatch):
    cs = create_session(client, skh)
    assert pay(client, cs["id"]).status_code == 200
    later(monkeypatch)
    assert expire(db) == 0
    assert get_session(client, skh, cs["id"])["status"] == "complete"
    assert retrieve_pi(client, skh, cs["payment_intent"])["status"] == "succeeded"


def test_expire_leaves_session_before_expires_at_alone(client, db, skh, monkeypatch):
    cs = create_session(client, skh)
    later(monkeypatch, timedelta(hours=23, minutes=59))
    assert expire(db) == 0
    assert get_session(client, skh, cs["id"])["status"] == "open"
    assert events(client, skh, "checkout.session.expired") == []


def test_expire_second_run_is_noop(client, db, skh, monkeypatch):
    create_session(client, skh)
    later(monkeypatch)
    assert expire(db) == 1
    assert expire(db) == 0
    assert len(events(client, skh, "checkout.session.expired")) == 1


# --- worker ---------------------------------------------------------------------------------


class _NoDb:
    """A session factory whose sessions are useless: every real job fails (and is isolated)."""

    @contextmanager
    def begin(self):
        yield None


def test_housekeeping_runs_checkout_expiry(monkeypatch):
    calls = []
    monkeypatch.setattr(checkout_mod, "expire_sessions", lambda db: calls.append(db) or 0)
    results = housekeeping.run_housekeeping(_NoDb())
    assert calls == [None]
    assert results["expired_checkout_sessions"] == 0


def test_checkout_expiry_runs_even_if_other_jobs_fail(monkeypatch):
    calls = []
    monkeypatch.setattr(checkout_mod, "expire_sessions", lambda db: calls.append(db) or 0)
    results = housekeeping.run_housekeeping(_NoDb())  # the real jobs fail on a None session
    assert results["released"] is None and calls == [None]
