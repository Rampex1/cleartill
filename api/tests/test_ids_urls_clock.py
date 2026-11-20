import re
from datetime import UTC, datetime, timedelta

import pytest

from app import clock, errors, ids, urls


def test_new_id_format():
    assert re.fullmatch(r"ch_[0-9A-Za-z]{24}", ids.new_id("ch_"))


def test_new_id_unique():
    assert len({ids.new_id("x_") for _ in range(1000)}) == 1000


def test_valid_https_url_returned(monkeypatch):
    monkeypatch.delenv("ALLOW_INSECURE_URLS", raising=False)
    url = "https://example.com/x?y=1"
    assert urls.validate_redirect_url(url, "success_url") == url


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "data:text/html,x",
        "http://example.com",
        "https:/nohost",
        " https://x.com",
        "https://x.com/\n",
        "https://x.com/" + "a" * 2048,
    ],
    ids=["javascript", "data", "http", "no_host", "leading_space", "newline", "too_long"],
)
def test_invalid_url_rejected(monkeypatch, url):
    monkeypatch.delenv("ALLOW_INSECURE_URLS", raising=False)
    with pytest.raises(errors.ApiError) as exc:
        urls.validate_redirect_url(url, "success_url")
    assert exc.value.status == 400
    assert exc.value.type == "invalid_request_error"
    assert exc.value.code == "url_invalid"
    assert exc.value.param == "success_url"


def test_http_allowed_when_insecure_enabled(monkeypatch):
    monkeypatch.setenv("ALLOW_INSECURE_URLS", "1")
    assert urls.validate_redirect_url("http://example.com", "p") == "http://example.com"


def test_clock_now_is_utc_aware():
    n = clock.now()
    assert n.tzinfo is not None
    assert n.utcoffset() == timedelta(0)


def test_clock_now_can_be_monkeypatched(monkeypatch):
    fixed = datetime(2030, 1, 1, tzinfo=UTC)
    monkeypatch.setattr(clock, "now", lambda: fixed)
    assert clock.now() == fixed
