"""M21: cleartill SDK unit tests (MockTransport, no server)."""

import hashlib
import hmac
import json
import time
import uuid
from urllib.parse import parse_qsl

import httpx2
import pytest

import cleartill

BASE = "http://api.test"


class Recorder:
    """MockTransport handler: replays `responses` in order (last one repeats), records requests.

    A response may be an exception instance, which is raised instead (transport failure)."""

    def __init__(self, *responses):
        self.responses = list(responses) or [httpx2.Response(200, json={"id": "x"})]
        self.requests: list[httpx2.Request] = []

    def __call__(self, request):
        self.requests.append(request)
        r = self.responses[min(len(self.requests), len(self.responses)) - 1]
        if isinstance(r, Exception):
            raise r
        return r


def make(*responses, api_key="sk_test_abc", **kwargs):
    rec = Recorder(*responses)
    http = httpx2.Client(transport=httpx2.MockTransport(rec), base_url=BASE)
    client = cleartill.Client(api_key, base_url=BASE, http_client=http, **kwargs)
    slept: list[float] = []
    client._sleep = lambda s: slept.append(s)
    return client, rec, slept


def ok(body=None, **kwargs):
    return httpx2.Response(200, json=body if body is not None else {"id": "x"}, **kwargs)


def error(status, type_="invalid_request_error", headers=None, **fields):
    body = {"error": {"type": type_, "message": f"msg {status}", **fields}}
    return httpx2.Response(status, json=body, headers={"Request-Id": "req_123", **(headers or {})})


def form(request):
    return parse_qsl(request.content.decode(), keep_blank_values=True)


# --- encoding ---------------------------------------------------------------------------------


def test_encode_scalar():
    assert cleartill.encode({"a": 1}) == [("a", "1")]


def test_encode_nested_dict():
    assert cleartill.encode({"metadata": {"k": "v"}}) == [("metadata[k]", "v")]


def test_encode_list_uses_indexes():
    assert cleartill.encode({"enabled_events": ["*", "x"]}) == [
        ("enabled_events[0]", "*"),
        ("enabled_events[1]", "x"),
    ]


def test_encode_list_of_nested_dicts():
    params = {"line_items": [{"price_data": {"currency": "usd"}, "quantity": 2}]}
    assert cleartill.encode(params) == [
        ("line_items[0][price_data][currency]", "usd"),
        ("line_items[0][quantity]", "2"),
    ]


def test_encode_booleans_are_lowercase():
    assert cleartill.encode({"a": True, "b": False}) == [("a", "true"), ("b", "false")]


def test_encode_drops_none():
    assert cleartill.encode({"a": None, "b": "x", "c": {"d": None}}) == [("b", "x")]


def test_encode_keeps_input_order():
    assert cleartill.encode({"z": 1, "a": 2, "m": 3}) == [("z", "1"), ("a", "2"), ("m", "3")]


def test_encode_empty():
    assert cleartill.encode({}) == []


def test_encode_unicode_and_reserved_characters_round_trip_in_body():
    client, rec, _ = make()
    client.customers.create(name="Zoë & Co=1", metadata={"note": "a+b/c?d"})
    assert form(rec.requests[0]) == [("name", "Zoë & Co=1"), ("metadata[note]", "a+b/c?d")]


# --- request shape ----------------------------------------------------------------------------


def test_post_sends_uuid4_idempotency_key():
    client, rec, _ = make()
    client.customers.create(email="a@example.com")
    key = rec.requests[0].headers["Idempotency-Key"]
    assert uuid.UUID(key).version == 4


def test_each_post_call_gets_a_fresh_idempotency_key():
    client, rec, _ = make()
    client.customers.create()
    client.customers.create()
    keys = {r.headers["Idempotency-Key"] for r in rec.requests}
    assert len(keys) == 2


def test_explicit_idempotency_key_is_sent():
    client, rec, _ = make()
    client.customers.create(email="a@example.com", idempotency_key="my-key-1")
    assert rec.requests[0].headers["Idempotency-Key"] == "my-key-1"
    assert ("idempotency_key", "my-key-1") not in form(rec.requests[0])


def test_get_sends_no_idempotency_key():
    client, rec, _ = make()
    client.customers.retrieve("cus_1")
    assert "Idempotency-Key" not in rec.requests[0].headers


def test_auth_user_agent_and_content_type_headers():
    client, rec, _ = make(api_key="sk_test_secret")
    client.customers.create(name="A")
    h = rec.requests[0].headers
    assert h["Authorization"] == "Bearer sk_test_secret"
    assert h["User-Agent"].startswith("cleartill-python/")
    assert h["Content-Type"].startswith("application/x-www-form-urlencoded")


def test_get_has_auth_and_user_agent():
    client, rec, _ = make(api_key="sk_test_secret")
    client.balance.retrieve()
    h = rec.requests[0].headers
    assert h["Authorization"] == "Bearer sk_test_secret"
    assert h["User-Agent"].startswith("cleartill-python/")


def test_post_body_is_form_encoded():
    client, rec, _ = make()
    client.customers.create(email="a@example.com", metadata={"k": "v"})
    assert rec.requests[0].method == "POST"
    assert form(rec.requests[0]) == [("email", "a@example.com"), ("metadata[k]", "v")]


def test_get_params_go_in_query_string():
    client, rec, _ = make(ok({"object": "list", "data": [], "has_more": False}))
    client.customers.list(limit=3, created={"gte": 10})
    req = rec.requests[0]
    assert req.method == "GET"
    assert req.url.path == "/v1/customers"
    assert req.url.params.multi_items() == [("limit", "3"), ("created[gte]", "10")]
    assert req.content == b""


@pytest.mark.parametrize(
    "call, method, path",
    [
        (lambda c: c.customers.create(), "POST", "/v1/customers"),
        (lambda c: c.customers.retrieve("cus_1"), "GET", "/v1/customers/cus_1"),
        (lambda c: c.customers.update("cus_1", name="n"), "POST", "/v1/customers/cus_1"),
        (lambda c: c.customers.delete("cus_1"), "DELETE", "/v1/customers/cus_1"),
        (lambda c: c.customers.list(), "GET", "/v1/customers"),
        (lambda c: c.payment_methods.create(type="card"), "POST", "/v1/payment_methods"),
        (lambda c: c.payment_methods.retrieve("pm_1"), "GET", "/v1/payment_methods/pm_1"),
        (lambda c: c.payment_intents.create(), "POST", "/v1/payment_intents"),
        (lambda c: c.payment_intents.retrieve("pi_1"), "GET", "/v1/payment_intents/pi_1"),
        (lambda c: c.payment_intents.update("pi_1"), "POST", "/v1/payment_intents/pi_1"),
        (lambda c: c.payment_intents.list(), "GET", "/v1/payment_intents"),
        (
            lambda c: c.payment_intents.confirm("pi_1"),
            "POST",
            "/v1/payment_intents/pi_1/confirm",
        ),
        (
            lambda c: c.payment_intents.capture("pi_1"),
            "POST",
            "/v1/payment_intents/pi_1/capture",
        ),
        (lambda c: c.payment_intents.cancel("pi_1"), "POST", "/v1/payment_intents/pi_1/cancel"),
        (lambda c: c.charges.retrieve("ch_1"), "GET", "/v1/charges/ch_1"),
        (lambda c: c.charges.list(), "GET", "/v1/charges"),
        (lambda c: c.refunds.create(), "POST", "/v1/refunds"),
        (lambda c: c.refunds.retrieve("re_1"), "GET", "/v1/refunds/re_1"),
        (lambda c: c.refunds.list(), "GET", "/v1/refunds"),
        (lambda c: c.balance.retrieve(), "GET", "/v1/balance"),
        (
            lambda c: c.balance_transactions.retrieve("txn_1"),
            "GET",
            "/v1/balance_transactions/txn_1",
        ),
        (lambda c: c.balance_transactions.list(), "GET", "/v1/balance_transactions"),
        (lambda c: c.events.retrieve("evt_1"), "GET", "/v1/events/evt_1"),
        (lambda c: c.events.list(), "GET", "/v1/events"),
        (lambda c: c.webhook_endpoints.create(), "POST", "/v1/webhook_endpoints"),
        (lambda c: c.webhook_endpoints.retrieve("we_1"), "GET", "/v1/webhook_endpoints/we_1"),
        (lambda c: c.webhook_endpoints.update("we_1"), "POST", "/v1/webhook_endpoints/we_1"),
        (lambda c: c.webhook_endpoints.delete("we_1"), "DELETE", "/v1/webhook_endpoints/we_1"),
        (lambda c: c.webhook_endpoints.list(), "GET", "/v1/webhook_endpoints"),
        (lambda c: c.checkout.sessions.create(), "POST", "/v1/checkout/sessions"),
        (lambda c: c.checkout.sessions.retrieve("cs_1"), "GET", "/v1/checkout/sessions/cs_1"),
        (lambda c: c.checkout.sessions.list(), "GET", "/v1/checkout/sessions"),
    ],
)
def test_method_and_path(call, method, path):
    client, rec, _ = make(ok({"object": "list", "data": [], "has_more": False}))
    call(client)
    assert (rec.requests[0].method, rec.requests[0].url.path) == (method, path)


def test_request_goes_to_base_url_host():
    client, rec, _ = make()
    client.customers.retrieve("cus_1")
    assert str(rec.requests[0].url).startswith(BASE + "/v1/")


def test_returns_plain_dict():
    client, _, _ = make(ok({"id": "cus_1", "object": "customer", "metadata": {"k": "v"}}))
    result = client.customers.retrieve("cus_1")
    assert type(result) is dict
    assert result == {"id": "cus_1", "object": "customer", "metadata": {"k": "v"}}


# --- retries ----------------------------------------------------------------------------------


def test_500_500_200_succeeds_with_same_key_and_two_sleeps():
    client, rec, slept = make(error(500, "api_error"), error(500, "api_error"), ok({"id": "c"}))
    assert client.customers.create(name="A") == {"id": "c"}
    assert len(rec.requests) == 3
    assert len({r.headers["Idempotency-Key"] for r in rec.requests}) == 1
    assert len(slept) == 2


def test_explicit_idempotency_key_reused_on_retry():
    client, rec, _ = make(error(503, "api_error"), ok())
    client.customers.create(idempotency_key="k-1")
    assert [r.headers["Idempotency-Key"] for r in rec.requests] == ["k-1", "k-1"]


def test_retry_resends_same_body():
    client, rec, _ = make(error(500, "api_error"), ok())
    client.customers.create(name="A", metadata={"k": "v"})
    assert form(rec.requests[0]) == form(rec.requests[1])


def test_backoff_within_bounds():
    client, _, slept = make(error(500, "api_error"), error(500, "api_error"), ok(), max_retries=2)
    client.customers.create()
    assert 0 <= slept[0] <= 0.5
    assert 0 <= slept[1] <= 1.0


def test_backoff_capped_at_8_seconds():
    client, _, slept = make(error(500, "api_error"), max_retries=6)
    with pytest.raises(cleartill.APIError):
        client.customers.retrieve("cus_1")
    assert len(slept) == 6
    assert all(0 <= s <= 8 for s in slept)
    for n, s in enumerate(slept):
        assert s <= min(8, 0.5 * 2**n)


def test_409_is_retried():
    client, rec, _ = make(error(409, "api_error"), ok())
    assert client.customers.create() == {"id": "x"}
    assert len(rec.requests) == 2


def test_429_is_retried():
    client, rec, _ = make(error(429, "rate_limit_error"), ok())
    assert client.customers.retrieve("cus_1") == {"id": "x"}
    assert len(rec.requests) == 2


def test_retry_after_header_sets_exact_sleep():
    client, _, slept = make(error(429, "rate_limit_error", headers={"Retry-After": "2"}), ok())
    client.customers.create()
    assert slept == [2]


def test_get_is_retried_on_500():
    client, rec, _ = make(error(502, "api_error"), ok())
    client.customers.retrieve("cus_1")
    assert len(rec.requests) == 2
    assert all("Idempotency-Key" not in r.headers for r in rec.requests)


def test_connect_error_then_200_succeeds():
    client, rec, slept = make(httpx2.ConnectError("refused"), ok({"id": "c"}))
    assert client.customers.create() == {"id": "c"}
    assert len(rec.requests) == 2
    assert len(slept) == 1
    assert rec.requests[0].headers["Idempotency-Key"] == rec.requests[1].headers["Idempotency-Key"]


def test_read_timeout_is_retried():
    client, rec, _ = make(httpx2.ReadTimeout("slow"), ok())
    assert client.customers.retrieve("cus_1") == {"id": "x"}
    assert len(rec.requests) == 2


def test_exhausted_500_raises_api_error():
    client, rec, slept = make(error(500, "api_error"))
    with pytest.raises(cleartill.APIError) as e:
        client.customers.create()
    assert e.value.http_status == 500
    assert len(rec.requests) == 3
    assert len(slept) == 2


def test_exhausted_transport_errors_raise_api_connection_error():
    client, rec, _ = make(httpx2.ConnectError("refused"))
    with pytest.raises(cleartill.APIConnectionError) as e:
        client.customers.create()
    assert isinstance(e.value, cleartill.CleartillError)
    assert len(rec.requests) == 3


def test_max_retries_zero_means_one_attempt():
    client, rec, slept = make(error(500, "api_error"), ok(), max_retries=0)
    with pytest.raises(cleartill.APIError):
        client.customers.create()
    assert len(rec.requests) == 1
    assert slept == []


def test_max_retries_zero_transport_error_one_attempt():
    client, rec, _ = make(httpx2.ConnectError("refused"), ok(), max_retries=0)
    with pytest.raises(cleartill.APIConnectionError):
        client.customers.retrieve("cus_1")
    assert len(rec.requests) == 1


def test_max_retries_custom_count():
    client, rec, _ = make(error(500, "api_error"), max_retries=4)
    with pytest.raises(cleartill.APIError):
        client.customers.create()
    assert len(rec.requests) == 5


@pytest.mark.parametrize(
    "status, type_",
    [
        (400, "invalid_request_error"),
        (401, "invalid_request_error"),
        (402, "card_error"),
        (403, "invalid_request_error"),
        (404, "invalid_request_error"),
    ],
)
def test_client_errors_are_not_retried(status, type_):
    client, rec, slept = make(error(status, type_), ok())
    with pytest.raises(cleartill.CleartillError):
        client.customers.create()
    assert len(rec.requests) == 1
    assert slept == []


# --- error mapping ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "status, type_, cls",
    [
        (400, "invalid_request_error", "InvalidRequestError"),
        (400, "idempotency_error", "IdempotencyError"),
        (409, "idempotency_error", "IdempotencyError"),
        (401, "invalid_request_error", "AuthenticationError"),
        (402, "card_error", "CardError"),
        (403, "invalid_request_error", "PermissionError"),
        (404, "invalid_request_error", "InvalidRequestError"),
        (409, "api_error", "APIError"),
        (429, "rate_limit_error", "RateLimitError"),
        (500, "api_error", "APIError"),
        (503, "api_error", "APIError"),
    ],
)
def test_error_mapping(status, type_, cls):
    cls = getattr(cleartill, cls)
    resp = error(status, type_, code="some_code", decline_code="some_decline", param="amount")
    client, _, _ = make(resp, max_retries=0)
    with pytest.raises(cls) as e:
        client.customers.create()
    err = e.value
    assert type(err) is cls
    assert isinstance(err, cleartill.CleartillError)
    assert err.http_status == status
    assert err.message == f"msg {status}"
    assert err.code == "some_code"
    assert err.decline_code == "some_decline"
    assert err.param == "amount"
    assert err.request_id == "req_123"
    assert err.json_body == json.loads(resp.content)


def test_card_error_fields():
    resp = error(
        402,
        "card_error",
        code="card_declined",
        decline_code="insufficient_funds",
        param="payment_method",
    )
    client, _, _ = make(resp)
    with pytest.raises(cleartill.CardError) as e:
        client.payment_intents.confirm("pi_1", payment_method="pm_x")
    assert (e.value.code, e.value.decline_code, e.value.param, e.value.http_status) == (
        "card_declined",
        "insufficient_funds",
        "payment_method",
        402,
    )


def test_missing_optional_error_fields_are_none():
    client, _, _ = make(error(404, "invalid_request_error"))
    with pytest.raises(cleartill.InvalidRequestError) as e:
        client.customers.retrieve("cus_missing")
    assert e.value.code is None
    assert e.value.decline_code is None
    assert e.value.param is None


def test_non_json_5xx_body_still_raises_api_error():
    client, _, _ = make(httpx2.Response(502, text="<html>bad gateway</html>"), max_retries=0)
    with pytest.raises(cleartill.APIError) as e:
        client.customers.retrieve("cus_1")
    assert e.value.http_status == 502


@pytest.mark.parametrize(
    "name",
    [
        "InvalidRequestError",
        "IdempotencyError",
        "AuthenticationError",
        "CardError",
        "PermissionError",
        "APIError",
        "RateLimitError",
        "APIConnectionError",
        "SignatureVerificationError",
    ],
)
def test_every_error_class_subclasses_cleartill_error(name):
    assert issubclass(getattr(cleartill, name), cleartill.CleartillError)


# --- list_all ---------------------------------------------------------------------------------


def page(ids, has_more):
    return ok({"object": "list", "data": [{"id": i} for i in ids], "has_more": has_more})


def test_list_all_walks_three_pages():
    client, rec, _ = make(
        page(["cus_1", "cus_2"], True), page(["cus_3", "cus_4"], True), page(["cus_5"], False)
    )
    items = client.customers.list_all(limit=2)
    assert not isinstance(items, list)  # generator, lazy
    assert [i["id"] for i in items] == ["cus_1", "cus_2", "cus_3", "cus_4", "cus_5"]
    assert len(rec.requests) == 3
    sent = [dict(r.url.params.multi_items()) for r in rec.requests]
    assert sent[0] == {"limit": "2"}
    assert sent[1] == {"limit": "2", "starting_after": "cus_2"}
    assert sent[2] == {"limit": "2", "starting_after": "cus_4"}
    assert all(r.url.path == "/v1/customers" for r in rec.requests)


def test_list_all_is_lazy():
    client, rec, _ = make(page(["a", "b"], True), page(["c"], False))
    gen = client.charges.list_all()
    assert rec.requests == []
    assert next(gen) == {"id": "a"}
    assert len(rec.requests) == 1


def test_list_all_single_empty_page():
    client, rec, _ = make(page([], False))
    assert list(client.events.list_all()) == []
    assert len(rec.requests) == 1


def test_list_all_on_checkout_sessions():
    client, rec, _ = make(page(["cs_1"], True), page(["cs_2"], False))
    assert [i["id"] for i in client.checkout.sessions.list_all()] == ["cs_1", "cs_2"]
    assert rec.requests[1].url.path == "/v1/checkout/sessions"
    assert rec.requests[1].url.params["starting_after"] == "cs_1"


# --- webhooks ---------------------------------------------------------------------------------

SECRET = "whsec_test_secret"
PAYLOAD = json.dumps({"id": "evt_1", "object": "event", "type": "payment_intent.succeeded"})
NOW = 1_700_000_000


def sig(payload, secret=SECRET, t=NOW):
    if isinstance(payload, bytes):
        payload = payload.decode()
    return hmac.new(secret.encode(), f"{t}.{payload}".encode(), hashlib.sha256).hexdigest()


def header(payload=PAYLOAD, secret=SECRET, t=NOW):
    return f"t={t},v1={sig(payload, secret, t)}"


def test_webhook_valid_signature_returns_event():
    event = cleartill.Webhook.construct_event(PAYLOAD, header(), SECRET, now=NOW)
    assert event == json.loads(PAYLOAD)


def test_webhook_bytes_payload():
    body = PAYLOAD.encode()
    event = cleartill.Webhook.construct_event(body, header(), SECRET, now=NOW)
    assert event["id"] == "evt_1"


def test_webhook_tampered_payload_rejected():
    tampered = PAYLOAD.replace("evt_1", "evt_2")
    with pytest.raises(cleartill.SignatureVerificationError):
        cleartill.Webhook.construct_event(tampered, header(), SECRET, now=NOW)


def test_webhook_wrong_secret_rejected():
    with pytest.raises(cleartill.SignatureVerificationError):
        cleartill.Webhook.construct_event(PAYLOAD, header(secret="whsec_other"), SECRET, now=NOW)


def test_webhook_expired_timestamp_rejected():
    with pytest.raises(cleartill.SignatureVerificationError):
        cleartill.Webhook.construct_event(PAYLOAD, header(), SECRET, now=NOW + 301)


def test_webhook_at_tolerance_boundary_accepted():
    event = cleartill.Webhook.construct_event(PAYLOAD, header(), SECRET, now=NOW + 300)
    assert event["id"] == "evt_1"


def test_webhook_custom_tolerance():
    with pytest.raises(cleartill.SignatureVerificationError):
        cleartill.Webhook.construct_event(PAYLOAD, header(), SECRET, tolerance=10, now=NOW + 11)


def test_webhook_default_now_is_current_time():
    t = int(time.time())
    event = cleartill.Webhook.construct_event(PAYLOAD, header(t=t), SECRET)
    assert event["id"] == "evt_1"


def test_webhook_timestamp_mismatch_with_signature_rejected():
    # Signature computed for NOW but header claims NOW + 1.
    bad = f"t={NOW + 1},v1={sig(PAYLOAD)}"
    with pytest.raises(cleartill.SignatureVerificationError):
        cleartill.Webhook.construct_event(PAYLOAD, bad, SECRET, now=NOW)


@pytest.mark.parametrize(
    "bad_header",
    [
        "",
        "garbage",
        f"v1={sig(PAYLOAD)}",  # no timestamp
        f"t={NOW}",  # no signature
        f"t=notanumber,v1={sig(PAYLOAD)}",
        f"t={NOW},v0={sig(PAYLOAD)}",  # unknown scheme only
    ],
)
def test_webhook_malformed_header_rejected(bad_header):
    with pytest.raises(cleartill.SignatureVerificationError):
        cleartill.Webhook.construct_event(PAYLOAD, bad_header, SECRET, now=NOW)


def test_webhook_multiple_v1_one_matches():
    h = f"t={NOW},v1={'0' * 64},v1={sig(PAYLOAD)}"
    event = cleartill.Webhook.construct_event(PAYLOAD, h, SECRET, now=NOW)
    assert event["id"] == "evt_1"


def test_webhook_multiple_v1_none_match():
    h = f"t={NOW},v1={'0' * 64},v1={'f' * 64}"
    with pytest.raises(cleartill.SignatureVerificationError):
        cleartill.Webhook.construct_event(PAYLOAD, h, SECRET, now=NOW)


def test_webhook_unicode_payload():
    payload = json.dumps({"id": "evt_ü", "note": "héllo ✓"}, ensure_ascii=False)
    event = cleartill.Webhook.construct_event(payload.encode(), header(payload), SECRET, now=NOW)
    assert event["note"] == "héllo ✓"
