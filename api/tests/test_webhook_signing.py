"""M14: Cleartill-Signature header signing and verification."""

import hashlib
import hmac

import pytest

from app import webhooks

SECRET = "whsec_test0123456789"
PAYLOAD = '{"id":"evt_1","object":"event"}'
T = 1_700_000_000


def test_signature_header_name():
    assert webhooks.SIGNATURE_HEADER == "Cleartill-Signature"


def test_sign_matches_the_hmac_formula():
    expected = hmac.new(SECRET.encode(), f"{T}.{PAYLOAD}".encode(), hashlib.sha256).hexdigest()
    assert webhooks.sign(SECRET, T, PAYLOAD) == f"t={T},v1={expected}"


def test_round_trip_verifies():
    header = webhooks.sign(SECRET, T, PAYLOAD)
    assert webhooks.verify(PAYLOAD, header, SECRET, now=T) is True


def test_round_trip_with_unicode_payload():
    payload = '{"name":"Zoë 🚀 \\u0000"}'
    header = webhooks.sign(SECRET, T, payload)
    assert webhooks.verify(payload, header, SECRET, now=T) is True


def test_round_trip_against_real_clock():
    import time

    now = int(time.time())
    assert webhooks.verify(PAYLOAD, webhooks.sign(SECRET, now, PAYLOAD), SECRET) is True


def test_tampered_payload_is_rejected():
    header = webhooks.sign(SECRET, T, PAYLOAD)
    with pytest.raises(webhooks.SignatureError):
        webhooks.verify(PAYLOAD + " ", header, SECRET, now=T)


def test_wrong_secret_is_rejected():
    header = webhooks.sign(SECRET, T, PAYLOAD)
    with pytest.raises(webhooks.SignatureError):
        webhooks.verify(PAYLOAD, header, "whsec_other", now=T)


def test_timestamp_301_seconds_old_is_rejected():
    header = webhooks.sign(SECRET, T, PAYLOAD)
    with pytest.raises(webhooks.SignatureError):
        webhooks.verify(PAYLOAD, header, SECRET, now=T + 301)


def test_timestamp_301_seconds_in_future_is_rejected():
    header = webhooks.sign(SECRET, T, PAYLOAD)
    with pytest.raises(webhooks.SignatureError):
        webhooks.verify(PAYLOAD, header, SECRET, now=T - 301)


def test_timestamp_exactly_at_tolerance_passes():
    header = webhooks.sign(SECRET, T, PAYLOAD)
    assert webhooks.verify(PAYLOAD, header, SECRET, now=T + 300) is True


def test_custom_tolerance_is_honoured():
    header = webhooks.sign(SECRET, T, PAYLOAD)
    with pytest.raises(webhooks.SignatureError):
        webhooks.verify(PAYLOAD, header, SECRET, tolerance=10, now=T + 11)


def test_one_valid_among_several_v1_passes():
    good = webhooks.sign(SECRET, T, PAYLOAD).split(",v1=")[1]
    header = f"t={T},v1={'0' * 64},v1={good},v1=deadbeef"
    assert webhooks.verify(PAYLOAD, header, SECRET, now=T) is True


def test_several_v1_none_valid_is_rejected():
    header = f"t={T},v1={'0' * 64},v1={'f' * 64}"
    with pytest.raises(webhooks.SignatureError):
        webhooks.verify(PAYLOAD, header, SECRET, now=T)


def test_unknown_v0_scheme_is_ignored():
    good = webhooks.sign(SECRET, T, PAYLOAD).split(",v1=")[1]
    header = f"t={T},v0={'a' * 64},v1={good}"
    assert webhooks.verify(PAYLOAD, header, SECRET, now=T) is True


def test_only_v0_scheme_is_rejected():
    good = webhooks.sign(SECRET, T, PAYLOAD).split(",v1=")[1]
    with pytest.raises(webhooks.SignatureError):
        webhooks.verify(PAYLOAD, f"t={T},v0={good}", SECRET, now=T)


@pytest.mark.parametrize(
    "header", ["", "nonsense", "t=,v1=", f"v1={'0' * 64}", "t=abc,v1=00", "t=1700000000"]
)
def test_missing_or_garbage_header_is_rejected(header):
    with pytest.raises(webhooks.SignatureError):
        webhooks.verify(PAYLOAD, header, SECRET, now=T)
