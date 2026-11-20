"""Create a merchant and print its test keys: python -m app.seed --email demo@cleartill.test"""

import argparse
import sys

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.merchants import create_merchant, normalize_email
from app.models import Merchant


def seed(db: Session, email: str):
    if db.scalar(select(Merchant.id).where(Merchant.email == normalize_email(email))):
        raise ValueError(f"A merchant with email {email} already exists.")
    return create_merchant(db, email)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--email", default="demo@cleartill.test")
    args = parser.parse_args(argv)
    try:
        with SessionLocal.begin() as db:
            merchant, secret_key, publishable_key = seed(db, args.email)
            merchant_id = merchant.id
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 1
    print(f"merchant:        {merchant_id}")
    print(f"secret key:      {secret_key}")
    print(f"publishable key: {publishable_key}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
