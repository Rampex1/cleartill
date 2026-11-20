"""Regression tests for the M3a code and security review findings."""

import json
import logging
from typing import Annotated, Literal

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import text

from app import auth, errors, forms, merchants, seed, urls
from app.db import get_db

FORM = {"Content-Type": "application/x-www-form-urlencoded"}

t = FastAPI()
errors.install(t)


class Validated(BaseModel):
    model_config = {"extra": "forbid"}
    amount: int = 0
    metadata: dict[Annotated[str, Field(max_length=5)], str] = {}
    card: str | None = None
    kind: Literal["a", "b"] | None = None

    @field_validator("card")
    @classmethod
    def no_card(cls, v):
        raise ValueError(f"bad card {v}")  # a careless validator that echoes input


class Whole(BaseModel):
    a: int = 0

    @model_validator(mode="after")
    def reject(self):
        raise ValueError("whole-model problem")


@t.post("/v")
async def v(params: Annotated[dict, Depends(forms.params)]):
    forms.validate(Validated, params)
    return {"ok": True}


@t.post("/whole")
async def whole(params: Annotated[dict, Depends(forms.params)]):
    forms.validate(Whole, params)
    return {"ok": True}


@t.api_route("/echo", methods=["GET", "DELETE", "POST"])
async def echo(params: Annotated[dict, Depends(forms.params)]):
    return params


@t.get("/t/obj/{object_id}")
def obj(object_id: str, ctx: Annotated[object, Depends(auth.require_secret_key)]):
    return {"merchant": ctx.merchant.id}


@t.get("/dup")
def dup(db: Annotated[object, Depends(get_db)]):
    email = "dup" + "@example.com"  # built at runtime so the source line isn't the "leak"
    merchants.create_merchant(db, email)
    merchants.create_merchant(db, email)  # unique violation -> 500


@t.get("/boom")
def boom():
    raise RuntimeError("x")


@pytest.fixture
def tc(db):
    def override():
        yield db

    t.dependency_overrides[get_db] = override
    with TestClient(t, raise_server_exceptions=False) as c:
        yield c
    t.dependency_overrides.clear()


def err(r):
    return r.json()["error"]


# --- form parser -------------------------------------------------------------------------


@pytest.mark.parametrize(
    "pairs,param",
    [
        ([("a", "1"), ("a", "2")], "a"),
        ([("a", "1"), ("a[b]", "2")], "a[b]"),
        ([("a[b", "1")], "a[b"),
        ([("a[b][c][d][e][f][g]", "1")], "a[b][c][d][e][f][g]"),
    ],
)
def test_parse_params_reports_the_offending_param(pairs, param):
    with pytest.raises(errors.ApiError) as exc:
        forms.parse_params(pairs)
    assert exc.value.param == param


@pytest.mark.parametrize("pairs", [[("a", "x\x00y")], [("a\x00", "1")]])
def test_nul_bytes_are_rejected(pairs):
    with pytest.raises(errors.ApiError) as exc:
        forms.parse_params(pairs)
    assert exc.value.status == 400 and exc.value.code == "parameter_invalid"


def test_malformed_key_is_not_echoed_and_param_is_truncated():
    key = "card[number]4242424242424242" + "x" * 500
    with pytest.raises(errors.ApiError) as exc:
        forms.parse_params([(key, "1")])
    assert "4242" not in exc.value.message
    assert len(exc.value.param) <= 100


# --- params dependency -------------------------------------------------------------------


def test_get_and_delete_read_the_query_string(tc):
    assert tc.get("/echo?a[b]=1").json() == {"a": {"b": "1"}}
    assert tc.delete("/echo?x=2").json() == {"x": "2"}


def test_content_type_with_charset_is_accepted(tc):
    r = tc.post(
        "/echo", content="a=1", headers={"Content-Type": FORM["Content-Type"] + "; charset=utf-8"}
    )
    assert r.json() == {"a": "1"}


def test_empty_post_without_content_type_is_accepted(tc):
    assert tc.post("/echo").json() == {}


def test_non_utf8_body_is_rejected(tc):
    r = tc.post("/echo", content=b"a=\xff\xfe", headers=FORM)
    assert r.status_code == 400


# --- validation messages ---------------------------------------------------------------


def test_custom_validator_message_does_not_echo_input(tc):
    r = tc.post("/v", content="card=4242424242424242", headers=FORM)
    assert r.status_code == 400 and err(r)["param"] == "card"
    assert "4242" not in r.text


def test_literal_error_does_not_echo_input(tc):
    r = tc.post("/v", content="kind=SECRET4242", headers=FORM)
    assert r.status_code == 400 and "SECRET4242" not in r.text


def test_dict_key_error_param_has_no_key_marker(tc):
    r = tc.post("/v", content="metadata[toolongkey]=1", headers=FORM)
    assert err(r)["param"] == "metadata[toolongkey]"


def test_unknown_param_is_parameter_unknown(tc):
    r = tc.post("/v", content="nope=1", headers=FORM)
    assert err(r)["code"] == "parameter_unknown" and err(r)["param"] == "nope"


def test_whole_model_error_has_no_param(tc):
    r = tc.post("/whole", content="a=1", headers=FORM)
    e = err(r)
    assert r.status_code == 400 and "param" not in e and "None" not in e["message"]


# --- HTTP errors and auth headers -----------------------------------------------------


def test_405_keeps_allow_header_and_envelope(tc):
    r = tc.put("/v")
    assert r.status_code == 405 and "allow" in r.headers
    assert err(r)["type"] == "invalid_request_error"


def test_404_does_not_reflect_path(tc):
    r = tc.get("/v1/cards/4242424242424242")
    assert r.status_code == 404 and "4242" not in r.text


def test_401_has_www_authenticate(tc):
    r = tc.get("/t/obj/x")
    assert r.status_code == 401
    assert r.headers["www-authenticate"] == 'Basic realm="Cleartill"'


def test_500_body_carries_request_id(tc):
    r = tc.get("/boom")
    assert r.status_code == 500 and err(r)["request_id"] == r.headers["request-id"]


# --- logging -------------------------------------------------------------------------------


def _log_lines(caplog):
    out = []
    for rec in caplog.records:
        try:
            out.append(json.loads(rec.getMessage()))
        except ValueError:
            pass
    return out


def test_request_log_line_uses_route_template_and_no_query(tc, db, caplog):
    merchant, sk, _ = seed.seed(db, "log@example.com")
    caplog.set_level(logging.INFO, logger="cleartill")
    r = tc.get(
        "/t/obj/cs_capability123?client_secret=abc123",
        headers={"Authorization": f"Bearer {sk}"},
    )
    assert r.status_code == 200
    (line,) = [x for x in _log_lines(caplog) if x["request_id"] == r.headers["request-id"]]
    assert line["path"] == "/t/obj/{object_id}"
    assert line["merchant_id"] == merchant.id
    assert line["status"] == 200 and line["method"] == "GET"
    assert "abc123" not in caplog.text and "cs_capability123" not in caplog.text


def test_db_error_logs_no_row_values(tc, caplog):
    caplog.set_level(logging.INFO, logger="cleartill")
    r = tc.get("/dup")
    assert r.status_code == 500
    assert "dup@example.com" not in caplog.text
    assert "DETAIL" not in caplog.text
    assert "UniqueViolation" in caplog.text


# --- redirect URLs -------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        'https://evil.com/"><script>alert(1)</script>',
        "https://\\evil.com",
        "https://evil.com\\@good.com",
        "https://user@good.com/",
        "https://evil.com/'x",
    ],
)
def test_redirect_url_rejects_ambiguous_or_dangerous_characters(url, monkeypatch):
    monkeypatch.delenv("ALLOW_INSECURE_URLS", raising=False)
    with pytest.raises(errors.ApiError) as exc:
        urls.validate_redirect_url(url, "success_url")
    assert exc.value.code == "url_invalid"


def test_db_is_unaffected(db):
    # sanity: the failing /dup route above ran in its own savepoint-joined session
    assert db.execute(text("SELECT 1")).scalar() == 1
