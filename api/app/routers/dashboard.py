"""Dashboard-only endpoints: signup, login, logout, session, API keys."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, BeforeValidator, ConfigDict, StringConstraints
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import clock, forms, ratelimit
from app.auth import AuthContext, require_dashboard_header, require_session
from app.db import get_db
from app.errors import ApiError
from app.executor import execute
from app.merchants import create_merchant, new_api_key, normalize_email
from app.models import ApiKey, Merchant
from app.passwords import DUMMY_HASH, verify_password
from app.sessions import (
    COOKIE_NAME,
    clear_session_cookie,
    create_session,
    delete_session,
    set_session_cookie,
)

router = APIRouter(prefix="/dashboard-api")

Db = Annotated[Session, Depends(get_db)]
Params = Annotated[dict, Depends(forms.params)]
SessionAuth = Annotated[AuthContext, Depends(require_session)]
DashboardHeader = [Depends(require_dashboard_header)]

_normalize = BeforeValidator(lambda v: normalize_email(v) if isinstance(v, str) else v)
Email = Annotated[str, _normalize, StringConstraints(max_length=254, pattern=r"^[^@\s]+@[^@\s]+$")]


class SignupParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: Email
    password: Annotated[str, StringConstraints(min_length=8, max_length=128)]


class LoginParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: Annotated[str, _normalize, StringConstraints(max_length=254)]
    password: Annotated[str, StringConstraints(max_length=128)]


def merchant_json(m: Merchant) -> dict:
    return {"id": m.id, "email": m.email}


def key_json(k: ApiKey) -> dict:
    return {
        "id": k.id,
        "object": "api_key",
        "kind": k.kind,
        "last4": k.last4,
        "created": int(k.created.timestamp()),
    }


@router.post("/signup", dependencies=DashboardHeader)
def signup(request: Request, db: Db, params: Params):
    ratelimit.check("signup", ratelimit.client_ip(request))
    session = {}

    def handler():
        data = forms.validate(SignupParams, params)
        if db.scalar(select(Merchant.id).where(Merchant.email == data.email)):
            raise ApiError(
                400,
                "invalid_request_error",
                "An account with this email already exists.",
                code="account_exists",
                param="email",
            )
        merchant, secret_key, publishable_key = create_merchant(
            db, data.email, password=data.password
        )
        session["token"] = create_session(db, merchant.id)
        return {
            "merchant": merchant_json(merchant),
            "secret_key": secret_key,
            "publishable_key": publishable_key,
        }

    response = execute(db, request, None, params, handler)
    set_session_cookie(response, session["token"])
    return response


@router.post("/login", dependencies=DashboardHeader)
def login(request: Request, db: Db, params: Params):
    ratelimit.check("login", ratelimit.client_ip(request))
    session = {}

    def handler():
        data = forms.validate(LoginParams, params)
        merchant = db.scalar(select(Merchant).where(Merchant.email == data.email))
        stored = merchant.password_hash if merchant and merchant.password_hash else None
        # Always run scrypt, so timing doesn't reveal whether the account exists.
        valid = verify_password(data.password, stored or DUMMY_HASH) and stored is not None
        if not valid:
            raise ApiError(
                401,
                "invalid_request_error",
                "Invalid email or password.",
                code="invalid_credentials",
            )
        session["token"] = create_session(db, merchant.id)  # always new: no fixation
        return {"merchant": merchant_json(merchant)}

    response = execute(db, request, None, params, handler)
    set_session_cookie(response, session["token"])
    return response


@router.post("/logout")
def logout(request: Request, auth: SessionAuth, db: Db, params: Params):
    def handler():
        delete_session(db, request.cookies[COOKIE_NAME])
        return {"logged_out": True}

    response = execute(db, request, auth.merchant.id, params, handler)
    clear_session_cookie(response)
    return response


@router.get("/session")
def current_session(auth: SessionAuth):
    return {"merchant": merchant_json(auth.merchant)}


@router.get("/keys")
def list_keys(auth: SessionAuth, db: Db):
    keys = db.scalars(
        select(ApiKey)
        .where(ApiKey.merchant_id == auth.merchant.id, ApiKey.revoked_at.is_(None))
        .order_by(ApiKey.seq.desc())
    )
    return {"object": "list", "data": [key_json(k) for k in keys]}


@router.post("/keys/{key_id}/roll")
def roll_key(key_id: str, request: Request, auth: SessionAuth, db: Db, params: Params):
    def handler():
        old = db.scalar(
            select(ApiKey)
            .where(
                ApiKey.id == key_id,
                ApiKey.merchant_id == auth.merchant.id,
                ApiKey.revoked_at.is_(None),
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if old is None:
            raise ApiError(
                404, "invalid_request_error", "No such API key.", code="resource_missing"
            )
        old.revoked_at = clock.now()  # revoked immediately
        new, secret = new_api_key(db, auth.merchant.id, old.kind)
        db.flush()
        return {**key_json(new), "secret": secret}

    return execute(db, request, auth.merchant.id, params, handler)
