"""Regression tests for the M21 review: quoted ids, capped Retry-After, webhook hygiene and
tolerance, empty values, floats, DELETE retries, close(), keyword-only keys, odd responses."""

import hashlib
import hmac

import httpx2
import pytest
from test_client import BASE, error, form, make, ok

import cleartill


def test_ids_are_quoted_so_they_cannot_change_the_path():
    client, rec, _ = make(ok())
    client.customers.delete("../webhook_endpoints/we_1")
    client.customers.retrieve("a?b=c")
    client.payment_intents.capture("pi/../x")
    paths = [r.url.raw_path.decode() for r in rec.requests]
    assert paths == [
        "/v1/customers/..%2Fwebhook_endpoints%2Fwe_1",
        "/v1/customers/a%3Fb%3Dc",
        "/v1/payment_intents/pi%2F..%2Fx/capture",
    ]


@pytest.mark.parametrize(("value", "slept"), [("86400", 8.0), ("-5", 0.0), ("2", 2.0)])
def test_retry_after_is_capped(value, slept):
    client, _, sleeps = make(error(503, "api_error", headers={"Retry-After": value}), ok())
    client.customers.list()
    assert sleeps == [slept]


@pytest.mark.parametrize("value", ["inf", "nan", "soon"])
def test_unusable_retry_after_falls_back_to_backoff(value):
    client, _, sleeps = make(error(503, "api_error", headers={"Retry-After": value}), ok())
    client.customers.list()
    assert len(sleeps) == 1 and 0 <= sleeps[0] <= 0.5


def sign(secret, t, payload: bytes):
    mac = hmac.new(secret.encode(), f"{t}.".encode() + payload, hashlib.sha256).hexdigest()
    return f"t={t},v1={mac}"


@pytest.mark.parametrize(
    ("payload", "header"),
    [
        ('{"id":"evt_1"}', "t=1,v1=é"),  # non-ASCII signature
        ('{"id":"evt_1"}', "t=²,v1=ab"),  # non-ASCII digit in t
        (b"\xff\xfe", None),  # non-UTF-8 bytes, validly signed
        ("not json", None),  # validly signed, not JSON
    ],
)
def test_bad_webhook_input_raises_only_signature_errors(payload, header):
    raw = payload if isinstance(payload, bytes) else payload.encode()
    header = header or sign("whsec_x", 1000, raw)
    with pytest.raises(cleartill.SignatureVerificationError):
        cleartill.Webhook.construct_event(payload, header, "whsec_x", now=1000)


def test_future_timestamps_are_bounded_like_the_server():
    raw = b'{"id":"evt_1"}'
    with pytest.raises(cleartill.SignatureVerificationError):
        cleartill.Webhook.construct_event(
            raw, sign("whsec_x", 1000 + 301, raw), "whsec_x", now=1000
        )
    assert cleartill.Webhook.construct_event(raw, sign("whsec_x", 1300, raw), "whsec_x", now=1000)


def test_zero_tolerance_means_exact_not_disabled():
    raw = b'{"id":"evt_1"}'
    with pytest.raises(cleartill.SignatureVerificationError):
        cleartill.Webhook.construct_event(raw, sign("whsec_x", 1, raw), "whsec_x", 0, now=1000)


def test_empty_dicts_and_lists_are_sent_not_dropped():
    assert cleartill.encode({"metadata": {}, "enabled_events": []}) == [
        ("metadata", ""),
        ("enabled_events", ""),
    ]
    client, rec, _ = make(ok())
    client.customers.update("cus_1", metadata={})
    assert form(rec.requests[0]) == [("metadata", "")]


def test_floats_are_rejected_client_side():
    with pytest.raises(TypeError):
        cleartill.encode({"amount": 20.0})


def test_delete_is_not_retried_after_a_5xx_or_read_timeout():
    client, rec, _ = make(error(500, "api_error"), ok())
    with pytest.raises(cleartill.APIError):
        client.customers.delete("cus_1")
    assert len(rec.requests) == 1
    client, rec, _ = make(httpx2.ReadTimeout("slow"), ok())
    with pytest.raises(cleartill.APIConnectionError):
        client.customers.delete("cus_1")
    assert len(rec.requests) == 1


def test_delete_is_retried_when_the_request_never_left():
    client, rec, _ = make(httpx2.ConnectError("refused"), ok({"id": "cus_1", "deleted": True}))
    assert client.customers.delete("cus_1")["deleted"] is True
    assert len(rec.requests) == 2


def test_close_and_context_manager_close_only_an_owned_client():
    with cleartill.Client("sk_test_x", base_url=BASE) as client:
        owned = client._http
    assert owned.is_closed
    client, _, _ = make(ok())
    client.close()
    assert not client._http.is_closed  # passed-in clients belong to the caller


def test_idempotency_key_is_keyword_only():
    client, _, _ = make(ok())
    with pytest.raises(TypeError):
        client.customers.create("oops")


def test_non_json_success_is_an_api_error_with_request_id():
    client, _, _ = make(httpx2.Response(200, text="<html>", headers={"Request-Id": "req_9"}))
    with pytest.raises(cleartill.APIError) as exc:
        client.customers.list()
    assert exc.value.request_id == "req_9"


def test_other_4xx_invalid_request_errors_map_to_invalid_request_error():
    client, _, _ = make(error(405))
    with pytest.raises(cleartill.InvalidRequestError):
        client.customers.list()


def test_negative_max_retries_is_rejected():
    with pytest.raises(ValueError):
        cleartill.Client("sk_test_x", max_retries=-1)


def test_list_all_refuses_ending_before():
    client, _, _ = make(ok())
    with pytest.raises(ValueError):
        next(client.customers.list_all(ending_before="cus_1"))


def test_permission_error_is_public_but_not_star_exported():
    assert cleartill.PermissionError is cleartill.CleartillPermissionError
    assert "PermissionError" not in cleartill.__all__
