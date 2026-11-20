"""M13: the SSRF guard on webhook endpoint URLs."""

import pytest

from app import seed, urls
from app.errors import ApiError


@pytest.fixture(autouse=True)
def secure(monkeypatch):
    monkeypatch.delenv("ALLOW_INSECURE_URLS", raising=False)


def resolver(monkeypatch, result):
    def fake(host, port):
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(urls, "_resolve", fake)


def assert_rejected(url):
    with pytest.raises(ApiError) as exc:
        urls.validate_webhook_url(url)
    e = exc.value
    assert (e.status, e.code, e.param) == (400, "url_invalid", "url")


# --- unit: rejected ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/",
        "http://10.0.0.1/",
        "http://169.254.169.254/",
        "http://[::1]/",
        "http://0.0.0.0/",
        "http://[::ffff:127.0.0.1]/",
        "http://[fd12::1]/",
        "http://100.64.0.1/",
        "http://192.168.1.1/",
        "http://172.16.0.1/",
        "http://[2002:7f00:1::]/",  # 6to4 wrapping 127.0.0.1
        "ftp://93.184.216.34/",
        "https://user@93.184.216.34/",
        "https://user:pw@93.184.216.34/",
        "https://93.184.216.34/a b",
        "https://93.184.216.34/\x01",
        "https:///nohost",
        "not a url",
        "",
        "https://93.184.216.34/" + "a" * 2048,
    ],
)
def test_rejects(url):
    assert_rejected(url)


def test_rejects_hostname_resolving_to_private_ip(monkeypatch):
    resolver(monkeypatch, ["10.0.0.5"])
    assert_rejected("https://hooks.example.com/")


def test_rejects_hostname_with_any_private_ip(monkeypatch):
    resolver(monkeypatch, ["93.184.216.34", "127.0.0.1"])
    assert_rejected("https://hooks.example.com/")


def test_rejects_hostname_that_does_not_resolve(monkeypatch):
    resolver(monkeypatch, OSError("nxdomain"))
    assert_rejected("https://hooks.example.com/")


def test_rejects_hostname_resolving_to_mapped_loopback(monkeypatch):
    resolver(monkeypatch, ["::ffff:127.0.0.1"])
    assert_rejected("https://hooks.example.com/")


# --- unit: accepted ------------------------------------------------------------------------


def test_accepts_public_ip_literal():
    assert urls.validate_webhook_url("https://93.184.216.34/") == ["93.184.216.34"]


def test_accepts_http_public_ip_literal():
    assert urls.validate_webhook_url("http://93.184.216.34:8080/hook") == ["93.184.216.34"]


def test_accepts_hostname_resolving_to_public_ip(monkeypatch):
    resolver(monkeypatch, ["93.184.216.34"])
    assert urls.validate_webhook_url("https://hooks.example.com/x?y=1") == ["93.184.216.34"]


def test_accepts_url_of_exactly_2048_chars():
    url = "https://93.184.216.34/"
    url += "a" * (2048 - len(url))
    assert urls.validate_webhook_url(url) == ["93.184.216.34"]


def test_insecure_mode_allows_loopback(monkeypatch):
    monkeypatch.setenv("ALLOW_INSECURE_URLS", "1")
    urls.validate_webhook_url("http://127.0.0.1/")


def test_insecure_mode_still_rejects_bad_scheme(monkeypatch):
    monkeypatch.setenv("ALLOW_INSECURE_URLS", "1")
    assert_rejected("ftp://127.0.0.1/")


# --- through the API -----------------------------------------------------------------------


@pytest.fixture
def h(db):
    _, sk, _ = seed.seed(db, "ssrf@example.com")
    return {"Authorization": f"Bearer {sk}"}


def assert_url_invalid(r):
    assert r.status_code == 400, r.text
    e = r.json()["error"]
    assert e["type"] == "invalid_request_error"
    assert e["code"] == "url_invalid" and e["param"] == "url"


def test_create_with_loopback_url_is_400(client, h):
    r = client.post(
        "/v1/webhook_endpoints",
        data={"url": "http://127.0.0.1/hook", "enabled_events[]": ["*"]},
        headers=h,
    )
    assert_url_invalid(r)
    assert client.get("/v1/webhook_endpoints", headers=h).json()["data"] == []


def test_update_to_loopback_url_is_400(client, h):
    good = "https://93.184.216.34/hook"
    r = client.post(
        "/v1/webhook_endpoints", data={"url": good, "enabled_events[]": ["*"]}, headers=h
    )
    assert r.status_code == 200, r.text
    p = f"/v1/webhook_endpoints/{r.json()['id']}"
    assert_url_invalid(client.post(p, data={"url": "http://127.0.0.1/hook"}, headers=h))
    assert client.get(p, headers=h).json()["url"] == good
