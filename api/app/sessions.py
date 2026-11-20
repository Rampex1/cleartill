"""Dashboard sessions: a random token in an HttpOnly cookie, stored only as its sha256."""

import hashlib
from datetime import timedelta

from fastapi import Response
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app import clock
from app.ids import random_string
from app.models import DashboardSession, Merchant

COOKIE_NAME = "cleartill_session"
SESSION_TTL = timedelta(days=7)  # fixed, not sliding: auth dependencies only read
MAX_TOKEN_LENGTH = 100


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(db: Session, merchant_id: str) -> str:
    token = random_string(43)  # ~256 bits
    now = clock.now()
    db.add(
        DashboardSession(
            token_hash=_hash(token),
            merchant_id=merchant_id,
            created=now,
            expires_at=now + SESSION_TTL,
        )
    )
    db.flush()
    return token


def merchant_for_token(db: Session, token: str | None) -> Merchant | None:
    if not token or len(token) > MAX_TOKEN_LENGTH:
        return None
    return db.scalar(
        select(Merchant)
        .join(DashboardSession, DashboardSession.merchant_id == Merchant.id)
        .where(
            DashboardSession.token_hash == _hash(token), DashboardSession.expires_at > clock.now()
        )
    )


def delete_session(db: Session, token: str) -> None:
    db.execute(delete(DashboardSession).where(DashboardSession.token_hash == _hash(token)))


def set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=int(SESSION_TTL.total_seconds()),
        httponly=True,
        secure=True,  # browsers treat http://localhost as secure, so local dev still works
        samesite="lax",
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(COOKIE_NAME, path="/", secure=True, httponly=True, samesite="lax")
