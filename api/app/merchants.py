import hashlib

from sqlalchemy.orm import Session

from app.ids import new_id, random_string
from app.models import ApiKey, Merchant


def hash_key(key: str) -> str:
    # Keys are 256-bit random strings, so a fast hash is enough (no KDF needed).
    return hashlib.sha256(key.encode()).hexdigest()


def _new_key(db: Session, merchant_id: str, kind: str, prefix: str) -> str:
    key = prefix + random_string(43)
    db.add(
        ApiKey(
            id=new_id("key_"),
            merchant_id=merchant_id,
            kind=kind,
            key_hash=hash_key(key),
            last4=key[-4:],
        )
    )
    return key


def create_merchant(db: Session, email: str) -> tuple[Merchant, str, str]:
    """Create a merchant with one secret and one publishable test key.

    The plaintext keys are returned once and never stored. Flushes, doesn't commit."""
    merchant = Merchant(id=new_id("acct_"), email=email)
    db.add(merchant)
    db.flush()
    secret_key = _new_key(db, merchant.id, "secret", "sk_test_")
    publishable_key = _new_key(db, merchant.id, "publishable", "pk_test_")
    db.flush()
    return merchant, secret_key, publishable_key
