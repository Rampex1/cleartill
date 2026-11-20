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
from app.sessions import COOKIE_NAME, merchant_for_token

CSRF_HEADER = "x-cleartill-dashboard"
SAFE_METHODS = {"GET", "HEAD"}


@dataclass
class AuthContext:
    merchant: Merchant
    kind: str  # "secret" | "publishable"
    via: str = "api_key"  # "api_key" | "session"


def check_csrf(request: Request) -> None:
    """Cookie-authenticated writes must carry a header a cross-site form can't set."""
    if request.method not in SAFE_METHODS and request.headers.get(CSRF_HEADER) != "1":
        raise ApiError(
            403,
            "invalid_request_error",
            "Dashboard requests must send the header X-Cleartill-Dashboard: 1.",
            code="permission_denied",
        )


def _session_context(request: Request, db: Session) -> AuthContext | None:
    merchant = merchant_for_token(db, request.cookies.get(COOKIE_NAME))
    if merchant is None:
        return None
    check_csrf(request)
    request.state.merchant_id = merchant.id
    return AuthContext(merchant=merchant, kind="secret", via="session")


def require_session(request: Request, db: Annotated[Session, Depends(get_db)]) -> AuthContext:
    """Dashboard-only endpoints: the session cookie, never an API key."""
    ctx = _session_context(request, db)
    if ctx is None:
        raise ApiError(
            401, "invalid_request_error", "You are not logged in.", code="not_authenticated"
        )
    return ctx


def require_dashboard_header(request: Request) -> None:
    """For signup/login, which have no session yet (blocks login CSRF)."""
    check_csrf(request)


def _invalid(message: str = "Invalid API key provided.") -> ApiError:
    # Never echo the key back.
    return ApiError(
        401,
        "invalid_request_error",
        message,
        code="api_key_invalid",
        headers={"WWW-Authenticate": 'Basic realm="Cleartill"'},
    )


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
    header = request.headers.get("authorization")
    if header is None:  # an Authorization header always wins over the cookie
        ctx = _session_context(request, db)
        if ctx is not None:
            return ctx
    key = _key_from_header(header)
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
