import hashlib
import unicodedata

from sqlalchemy.orm import Session

from app.ids import new_id, random_string
from app.models import ApiKey, Merchant
from app.passwords import hash_password


def normalize_email(email: str) -> str:
    return unicodedata.normalize("NFKC", email).strip().casefold()


def hash_key(key: str) -> str:
    # Keys are 256-bit random strings, so a fast hash is enough (no KDF needed).
    return hashlib.sha256(key.encode()).hexdigest()


KEY_PREFIXES = {"secret": "sk_test_", "publishable": "pk_test_"}


def new_api_key(db: Session, merchant_id: str, kind: str) -> tuple[ApiKey, str]:
    """Create a key; returns the row and the plaintext (shown once, never stored)."""
    key = KEY_PREFIXES[kind] + random_string(43)
    row = ApiKey(
        id=new_id("key_"),
        merchant_id=merchant_id,
        kind=kind,
        key_hash=hash_key(key),
        last4=key[-4:],
    )
    db.add(row)
    return row, key


def create_merchant(
    db: Session, email: str, password: str | None = None
) -> tuple[Merchant, str, str]:
    """Create a merchant with one secret and one publishable test key.

    The plaintext keys are returned once and never stored. Flushes, doesn't commit."""
    merchant = Merchant(
        id=new_id("acct_"),
        email=normalize_email(email),
        password_hash=hash_password(password) if password is not None else None,
    )
    db.add(merchant)
    db.flush()
    _, secret_key = new_api_key(db, merchant.id, "secret")
    _, publishable_key = new_api_key(db, merchant.id, "publishable")
    db.flush()
    return merchant, secret_key, publishable_key
