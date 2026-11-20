import base64
import binascii
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.errors import ApiError
from app.merchants import hash_key
from app.models import ApiKey, Merchant


@dataclass
class AuthContext:
    merchant: Merchant
    kind: str  # "secret" | "publishable"


def _invalid(message: str = "Invalid API key provided.") -> ApiError:
    # Never echo the key back.
    return ApiError(401, "invalid_request_error", message, code="api_key_invalid")


def _key_from_header(header: str | None) -> str:
    if not header:
        raise _invalid(
            "You did not provide an API key. "
            "Send it as the HTTP Basic username or as a Bearer token."
        )
    scheme, _, value = header.partition(" ")
    scheme, value = scheme.lower(), value.strip()
    if scheme == "bearer":
        key = value
    elif scheme == "basic":
        try:
            key = base64.b64decode(value, validate=True).decode().partition(":")[0]
        except (binascii.Error, UnicodeDecodeError):
            raise _invalid() from None
    else:
        raise _invalid()
    if not key:
        raise _invalid()
    return key


def authenticate(request: Request, db: Annotated[Session, Depends(get_db)]) -> AuthContext:
    key = _key_from_header(request.headers.get("authorization"))
    row = db.execute(
        select(ApiKey.kind, Merchant)
        .join(Merchant, Merchant.id == ApiKey.merchant_id)
        .where(ApiKey.key_hash == hash_key(key), ApiKey.revoked_at.is_(None))
    ).first()
    if row is None:
        raise _invalid()
    kind, merchant = row
    request.state.merchant_id = merchant.id
    return AuthContext(merchant=merchant, kind=kind)


def require_secret_key(ctx: Annotated[AuthContext, Depends(authenticate)]) -> AuthContext:
    if ctx.kind != "secret":
        raise ApiError(
            403,
            "invalid_request_error",
            "This request needs a secret key (sk_test_…).",
            code="permission_denied",
        )
    return ctx


def require_publishable_key(ctx: Annotated[AuthContext, Depends(authenticate)]) -> AuthContext:
    if ctx.kind != "publishable":
        raise ApiError(
            403,
            "invalid_request_error",
            "This request needs a publishable key (pk_test_…).",
            code="permission_denied",
        )
    return ctx
