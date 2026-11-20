"""M16: the 3DS challenge endpoint POST /checkout-api/3ds/{pi_id} (approve / fail)."""

from datetime import UTC, datetime
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest

from app import ledger, seed
from app.ledger_check import ledger_check

FORM = {"Content-Type": "application/x-www-form-urlencoded"}
EXP_YEAR = str(datetime.now(UTC).year + 5)
RETURN_URL = "https://example.com/done"
THREE_DS = ["pm_card_authenticationRequired", "4000002760003184"]


@pytest.fixture(autouse=True)
def ledger_clean(db):
    yield
    assert ledger_check(db, own_txn=False) == []


@pytest.fixture
def keys(db):
    return seed.seed(db, "threeds-a@example.com")


@pytest.fixture
def skh(keys):
    return {"Authorization": f"Bearer {keys[1]}", **FORM}


@pytest.fixture
def pkh(keys):
    return {"Authorization": f"Bearer {keys[2]}", **FORM}


def err(r):
    return r.json()["error"]


def post(client, h, path, **data):
    return client.post(path, content=urlencode(data), headers=h)


def create(client, h, **data):
    data = {"amount": "1000", "currency": "usd", **data}
    r = post(client, h, "/v1/payment_intents", **data)
    assert r.status_code == 200, r.text
    return r.json()


def confirm(client, h, pid, **data):
    return post(client, h, f"/v1/payment_intents/{pid}/confirm", **data)


def retrieve(client, h, pid):
    r = client.get(f"/v1/payment_intents/{pid}", headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def tokenize(client, pkh, number):
    body = (
        f"type=card&card[number]={number}&card[exp_month]=12"
        f"&card[exp_year]={EXP_YEAR}&card[cvc]=123"
    )
    r = client.post("/v1/payment_methods", content=body, headers=pkh)
    assert r.status_code == 200, r.text
    return r.json()["id"]


def pm_param(client, pkh, card):
    return card if card.startswith("pm_") else tokenize(client, pkh, card)


def charges_for(client, h, pid):
    r = client.get("/v1/charges", params={"payment_intent": pid}, headers=h)
    assert r.status_code == 200, r.text
    return r.json()["data"]


def events(client, h, type):
    r = client.get("/v1/events", params={"type": type}, headers=h)
    assert r.status_code == 200, r.text
    return r.json()["data"]


def assert_400(r, code=None, param=None):
    assert r.status_code == 400, r.text
    e = err(r)
    assert e["type"] == "invalid_request_error"
    if code is not None:
        assert e.get("code") == code
    if param is not None:
        assert e["param"] == param


def challenge(client, pid, **data):
    """The 3DS page posts form data with no Authorization header."""
    return client.post(f"/checkout-api/3ds/{pid}", content=urlencode(data), headers=FORM)


def requires_action(client, skh, pkh, card="pm_card_authenticationRequired", **extra):
    """A PI confirmed with a 3DS card: returns (created PI, requires_action PI)."""
    return_url = extra.pop("return_url", RETURN_URL)
    pi = create(client, skh, **extra)
    pm = pm_param(client, pkh, card)
    r = confirm(client, skh, pi["id"], payment_method=pm, return_url=return_url)
    assert r.status_code == 200, r.text
    got = r.json()
    assert got["status"] == "requires_action"
    return pi, got


def redirect_parts(r):
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) == {"redirect"}
    u = urlsplit(body["redirect"])
    return f"{u.scheme}://{u.netloc}{u.path}", parse_qs(u.query, keep_blank_values=True)


# --- approve --------------------------------------------------------------------------------


@pytest.mark.parametrize("card", THREE_DS)
def test_approve_automatic_capture_redirects(client, skh, pkh, card):
    pi, _ = requires_action(client, skh, pkh, card)
    r = challenge(client, pi["id"], client_secret=pi["client_secret"], result="approve")
    assert r.status_code == 200, r.text
    assert r.json() == {"redirect": f"{RETURN_URL}?payment_intent={pi['id']}"}


@pytest.mark.parametrize("card", THREE_DS)
def test_approve_automatic_capture_succeeds_pi(client, skh, pkh, card):
    pi, _ = requires_action(client, skh, pkh, card)
    r = challenge(client, pi["id"], client_secret=pi["client_secret"], result="approve")
    assert r.status_code == 200, r.text
    got = retrieve(client, skh, pi["id"])
    assert got["status"] == "succeeded"
    assert got["amount_received"] == got["amount"] == 1000
    assert got["next_action"] is None
    assert got["latest_charge"] is not None


def test_approve_automatic_capture_creates_captured_charge(client, skh, pkh):
    pi, _ = requires_action(client, skh, pkh)
    r = challenge(client, pi["id"], client_secret=pi["client_secret"], result="approve")
    assert r.status_code == 200, r.text
    (ch,) = charges_for(client, skh, pi["id"])
    assert ch["id"] == retrieve(client, skh, pi["id"])["latest_charge"]
    assert ch["status"] == "succeeded"
    assert ch["captured"] is True


def test_approve_automatic_capture_emits_events(client, skh, pkh):
    pi, _ = requires_action(client, skh, pkh)
    r = challenge(client, pi["id"], client_secret=pi["client_secret"], result="approve")
    assert r.status_code == 200, r.text
    assert len(events(client, skh, "charge.succeeded")) == 1
    (e,) = events(client, skh, "payment_intent.succeeded")
    assert e["data"]["object"]["id"] == pi["id"]


def test_approve_automatic_capture_posts_pending_balance(client, db, keys, skh, pkh):
    pi, _ = requires_action(client, skh, pkh)
    r = challenge(client, pi["id"], client_secret=pi["client_secret"], result="approve")
    assert r.status_code == 200, r.text
    assert ledger.balance(db, keys[0].id)["pending"] == [
        {"amount": 1000 - ledger.fee(1000), "currency": "usd"}
    ]


def test_approve_manual_capture_requires_capture(client, skh, pkh):
    pi, _ = requires_action(client, skh, pkh, capture_method="manual")
    r = challenge(client, pi["id"], client_secret=pi["client_secret"], result="approve")
    assert r.status_code == 200, r.text
    got = retrieve(client, skh, pi["id"])
    assert got["status"] == "requires_capture"
    assert got["amount_capturable"] == 1000
    assert len(events(client, skh, "payment_intent.amount_capturable_updated")) == 1


# --- fail -----------------------------------------------------------------------------------


@pytest.mark.parametrize("card", THREE_DS)
def test_fail_redirects(client, skh, pkh, card):
    pi, _ = requires_action(client, skh, pkh, card)
    r = challenge(client, pi["id"], client_secret=pi["client_secret"], result="fail")
    assert r.status_code == 200, r.text
    assert r.json() == {"redirect": f"{RETURN_URL}?payment_intent={pi['id']}"}


@pytest.mark.parametrize("card", THREE_DS)
def test_fail_returns_pi_to_requires_payment_method(client, skh, pkh, card):
    pi, ra = requires_action(client, skh, pkh, card)
    r = challenge(client, pi["id"], client_secret=pi["client_secret"], result="fail")
    assert r.status_code == 200, r.text
    got = retrieve(client, skh, pi["id"])
    assert got["status"] == "requires_payment_method"
    assert got["payment_method"] is None
    lpe = got["last_payment_error"]
    assert lpe["code"] == "payment_intent_authentication_failure"
    assert lpe["type"] == "invalid_request_error"
    assert lpe["payment_method"]["id"] == ra["payment_method"]


def test_fail_creates_failed_charge(client, skh, pkh):
    pi, _ = requires_action(client, skh, pkh)
    r = challenge(client, pi["id"], client_secret=pi["client_secret"], result="fail")
    assert r.status_code == 200, r.text
    (ch,) = charges_for(client, skh, pi["id"])
    assert ch["status"] == "failed"
    assert ch["failure_code"] == "authentication_required"


def test_fail_emits_events(client, skh, pkh):
    pi, _ = requires_action(client, skh, pkh)
    r = challenge(client, pi["id"], client_secret=pi["client_secret"], result="fail")
    assert r.status_code == 200, r.text
    assert len(events(client, skh, "charge.failed")) == 1
    assert len(events(client, skh, "payment_intent.payment_failed")) == 1


def test_fail_posts_no_ledger_entries(client, db, keys, skh, pkh):
    pi, _ = requires_action(client, skh, pkh)
    r = challenge(client, pi["id"], client_secret=pi["client_secret"], result="fail")
    assert r.status_code == 200, r.text
    assert ledger.balance(db, keys[0].id)["pending"] == [{"amount": 0, "currency": "usd"}]


def test_retry_after_fail_succeeds(client, skh, pkh):
    pi, _ = requires_action(client, skh, pkh)
    r = challenge(client, pi["id"], client_secret=pi["client_secret"], result="fail")
    assert r.status_code == 200, r.text
    r = confirm(client, skh, pi["id"], payment_method="pm_card_visa")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "succeeded"


# --- return_url query -----------------------------------------------------------------------


@pytest.mark.parametrize("result", ["approve", "fail"])
def test_redirect_merges_return_url_query(client, skh, pkh, result):
    pi, _ = requires_action(client, skh, pkh, return_url="https://example.com/done?order=7")
    r = challenge(client, pi["id"], client_secret=pi["client_secret"], result=result)
    base, query = redirect_parts(r)
    assert base == "https://example.com/done"
    assert query == {"order": ["7"], "payment_intent": [pi["id"]]}


# --- not found ------------------------------------------------------------------------------


def assert_404(r):
    assert r.status_code == 404, r.text
    e = err(r)
    assert e["type"] == "invalid_request_error"
    assert e.get("code") == "resource_missing"
    return e


def test_other_pis_secret_is_404_without_leak(client, skh, pkh):
    pi, _ = requires_action(client, skh, pkh)
    other = create(client, skh)
    r = challenge(client, pi["id"], client_secret=other["client_secret"], result="approve")
    assert_404(r)
    assert other["client_secret"] not in r.text
    assert retrieve(client, skh, pi["id"])["status"] == "requires_action"
    assert charges_for(client, skh, pi["id"]) == []


def test_garbage_secret_is_404_without_leak(client, skh, pkh):
    pi, _ = requires_action(client, skh, pkh)
    secret = "pi_garbage_secret_<script>é"
    r = challenge(client, pi["id"], client_secret=secret, result="fail")
    assert_404(r)
    assert secret not in r.text
    got = retrieve(client, skh, pi["id"])
    assert got["status"] == "requires_action"
    assert got["next_action"] is not None
    assert charges_for(client, skh, pi["id"]) == []


def test_unknown_pi_is_same_404_as_wrong_secret(client, skh, pkh):
    pi, _ = requires_action(client, skh, pkh)
    wrong = challenge(client, pi["id"], client_secret="pi_x_secret_y", result="approve")
    unknown = challenge(
        client, "pi_doesnotexist0000", client_secret=pi["client_secret"], result="approve"
    )
    e_wrong, e_unknown = assert_404(wrong), assert_404(unknown)
    assert e_unknown["message"] == e_wrong["message"]
    assert pi["client_secret"] not in unknown.text
    assert retrieve(client, skh, pi["id"])["status"] == "requires_action"


# --- wrong state ----------------------------------------------------------------------------


def test_second_post_after_approve_is_unexpected_state(client, skh, pkh):
    pi, _ = requires_action(client, skh, pkh)
    r = challenge(client, pi["id"], client_secret=pi["client_secret"], result="approve")
    assert r.status_code == 200, r.text
    r = challenge(client, pi["id"], client_secret=pi["client_secret"], result="approve")
    assert_400(r, "payment_intent_unexpected_state")
    assert len(charges_for(client, skh, pi["id"])) == 1


def test_succeeded_pi_is_unexpected_state(client, skh):
    pi = create(client, skh)
    assert confirm(client, skh, pi["id"], payment_method="pm_card_visa").status_code == 200
    r = challenge(client, pi["id"], client_secret=pi["client_secret"], result="approve")
    assert_400(r, "payment_intent_unexpected_state")
    assert len(charges_for(client, skh, pi["id"])) == 1


def test_unconfirmed_pi_is_unexpected_state(client, skh):
    pi = create(client, skh)
    r = challenge(client, pi["id"], client_secret=pi["client_secret"], result="fail")
    assert_400(r, "payment_intent_unexpected_state")
    assert charges_for(client, skh, pi["id"]) == []
    assert retrieve(client, skh, pi["id"])["status"] == "requires_payment_method"


# --- validation -----------------------------------------------------------------------------


def test_missing_result_is_400(client, skh, pkh):
    pi, _ = requires_action(client, skh, pkh)
    assert_400(challenge(client, pi["id"], client_secret=pi["client_secret"]), param="result")


def test_invalid_result_is_400(client, skh, pkh):
    pi, _ = requires_action(client, skh, pkh)
    r = challenge(client, pi["id"], client_secret=pi["client_secret"], result="maybe")
    assert_400(r, param="result")
    assert retrieve(client, skh, pi["id"])["status"] == "requires_action"


def test_missing_client_secret_is_400(client, skh, pkh):
    pi, _ = requires_action(client, skh, pkh)
    assert_400(challenge(client, pi["id"], result="approve"), param="client_secret")


def test_unknown_field_is_400(client, skh, pkh):
    pi, _ = requires_action(client, skh, pkh)
    r = challenge(client, pi["id"], client_secret=pi["client_secret"], result="approve", amount="1")
    assert_400(r, "parameter_unknown")
    assert retrieve(client, skh, pi["id"])["status"] == "requires_action"


# --- tenancy --------------------------------------------------------------------------------


def test_works_across_tenants_with_only_client_secret(client, db, skh, pkh):
    other = seed.seed(db, "threeds-b@example.com")
    other_skh = {"Authorization": f"Bearer {other[1]}", **FORM}
    create(client, other_skh)  # merchant B exists and has its own PI
    pi, _ = requires_action(client, skh, pkh)
    r = challenge(client, pi["id"], client_secret=pi["client_secret"], result="approve")
    assert r.status_code == 200, r.text
    assert retrieve(client, skh, pi["id"])["status"] == "succeeded"


def test_other_tenants_secret_is_404(client, db, skh, pkh):
    other = seed.seed(db, "threeds-b@example.com")
    other_pi = create(client, {"Authorization": f"Bearer {other[1]}", **FORM})
    pi, _ = requires_action(client, skh, pkh)
    r = challenge(client, pi["id"], client_secret=other_pi["client_secret"], result="approve")
    assert_404(r)
    assert retrieve(client, skh, pi["id"])["status"] == "requires_action"


# --- confirm from requires_action -----------------------------------------------------------


def test_confirm_from_requires_action_with_new_card_succeeds(client, skh, pkh):
    pi, _ = requires_action(client, skh, pkh)
    r = confirm(client, skh, pi["id"], payment_method="pm_card_visa")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "succeeded"
    (ch,) = charges_for(client, skh, pi["id"])
    assert ch["status"] == "succeeded"


@pytest.mark.parametrize("card", THREE_DS)
def test_confirm_from_requires_action_with_same_3ds_pm_requires_action_again(
    client, skh, pkh, card
):
    pi, ra = requires_action(client, skh, pkh, card)
    r = confirm(client, skh, pi["id"], payment_method=ra["payment_method"])
    assert r.status_code == 200, r.text
    got = r.json()
    assert got["status"] == "requires_action"
    assert got["next_action"]["type"] == "redirect_to_url"
    assert f"/checkout/3ds/{pi['id']}" in got["next_action"]["redirect_to_url"]["url"]
    assert charges_for(client, skh, pi["id"]) == []
