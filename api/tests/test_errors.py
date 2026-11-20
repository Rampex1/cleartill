import re
from typing import Annotated

import psycopg
import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy.exc import OperationalError

from app import errors, forms
from app.db import get_db

FORM = {"Content-Type": "application/x-www-form-urlencoded"}
REQ_ID = re.compile(r"^req_[0-9A-Za-z]{24}$")


class M(BaseModel):
    amount: int
    metadata: dict[str, int] = {}


t = FastAPI()
errors.install(t)


@t.post("/validate")
async def validate(params: Annotated[dict, Depends(forms.params)]):
    forms.validate(M, params)
    return {"ok": True}


@t.get("/server-bug")
def server_bug():
    M.model_validate({"amount": "4242424242424242x"})


@t.get("/ok")
def ok():
    return {"ok": True}


@t.get("/card")
def card():
    raise errors.ApiError(402, "card_error", "Declined", code="card_declined")


@t.get("/lock")
def lock():
    raise OperationalError("SELECT 1", {}, psycopg.errors.LockNotAvailable("x"))


@t.get("/deadlock")
def deadlock():
    raise OperationalError("SELECT 1", {}, psycopg.errors.DeadlockDetected("x"))


@t.get("/boom")
def boom():
    raise RuntimeError("secret detail")


@pytest.fixture
def tc(db):
    def override():
        yield db

    t.dependency_overrides[get_db] = override
    with TestClient(t, raise_server_exceptions=False) as c:
        yield c
    t.dependency_overrides.clear()


def error(resp):
    return resp.json()["error"]


def test_missing_field_is_parameter_missing(tc):
    r = tc.post("/validate", content="", headers=FORM)
    assert r.status_code == 400
    e = error(r)
    assert e["type"] == "invalid_request_error"
    assert e["code"] == "parameter_missing"
    assert e["param"] == "amount"


def test_invalid_value_is_parameter_invalid_and_not_echoed(tc):
    r = tc.post("/validate", content="amount=4242424242424242x", headers=FORM)
    assert r.status_code == 400
    e = error(r)
    assert e["type"] == "invalid_request_error"
    assert e["code"] == "parameter_invalid"
    assert e["param"] == "amount"
    assert "4242" not in r.text


def test_nested_param_uses_bracket_notation(tc):
    r = tc.post("/validate", content="amount=1&metadata[k]=notint", headers=FORM)
    assert r.status_code == 400
    assert error(r)["code"] == "parameter_invalid"
    assert error(r)["param"] == "metadata[k]"
    assert "notint" not in r.text


def test_valid_body_returns_200(tc):
    r = tc.post("/validate", content="amount=100&metadata[k]=1", headers=FORM)
    assert r.status_code == 200
    assert r.json() == {"ok": True}


def test_json_content_type_rejected(tc):
    r = tc.post("/validate", content='{"amount": 1}', headers={"Content-Type": "application/json"})
    assert r.status_code == 400
    assert error(r)["type"] == "invalid_request_error"


def test_oversized_body_rejected(tc):
    r = tc.post("/validate", content="amount=1&x=" + "a" * (65 * 1024), headers=FORM)
    assert r.status_code == 400
    assert error(r)["type"] == "invalid_request_error"


def test_unknown_route_is_404_envelope(tc):
    r = tc.get("/nope")
    assert r.status_code == 404
    assert error(r)["type"] == "invalid_request_error"
    assert error(r)["message"]


def test_api_error_rendered_exactly(tc):
    r = tc.get("/card")
    assert r.status_code == 402
    assert r.json() == {
        "error": {"type": "card_error", "message": "Declined", "code": "card_declined"}
    }


@pytest.mark.parametrize("path", ["/lock", "/deadlock"])
def test_lock_errors_are_409(tc, path):
    r = tc.get(path)
    assert r.status_code == 409
    assert error(r)["type"] == "api_error"
    assert error(r)["code"] == "lock_timeout"


def test_unhandled_exception_is_500_without_detail(tc):
    r = tc.get("/boom")
    assert r.status_code == 500
    assert error(r)["type"] == "api_error"
    assert "secret detail" not in r.text


def test_server_side_validation_error_is_500(tc):
    r = tc.get("/server-bug")
    assert r.status_code == 500
    assert error(r)["type"] == "api_error"
    assert "4242" not in r.text


@pytest.mark.parametrize(
    ("method", "path", "status"),
    [("get", "/ok", 200), ("post", "/validate", 400), ("get", "/nope", 404), ("get", "/boom", 500)],
)
def test_request_id_header_format(tc, method, path, status):
    r = tc.request(method.upper(), path, content="", headers=FORM)
    assert r.status_code == status
    assert REQ_ID.match(r.headers["Request-Id"])


def test_request_ids_are_unique(tc):
    assert tc.get("/ok").headers["Request-Id"] != tc.get("/ok").headers["Request-Id"]
