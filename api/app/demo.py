"""Demo accounts: a fresh, pre-populated merchant per visitor (no password, no sharing).

The history is made by the real business logic run "as of" past moments (clock.at), in time
order: creates, confirms, captures, releases on schedule, refunds and payouts. Every ledger
invariant holds exactly as it would for live traffic; nothing is inserted behind the API's back.
"""

import secrets
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import clock, ledger
from app.customers import customer_json
from app.events import emit
from app.ids import new_id
from app.merchants import create_merchant
from app.models import BalanceTransaction, Customer, Merchant
from app.payment_intents import capture_pi, confirm_pi, create_pi, resolve_payment_method
from app.payouts import available, create_payout, payout_json
from app.refunds import create_refund

SANDBOX_DOMAIN = "sandbox.cleartill.test"

CUSTOMERS = [
    ("Ada Lovelace", "ada@analytical.example"),
    ("Grace Hopper", "grace@cobol.example"),
    ("Alan Turing", "alan@bletchley.example"),
    ("Katherine Johnson", "katherine@nasa.example"),
    ("Margaret Hamilton", "margaret@apollo.example"),
    ("Linus Pauling", "linus@chem.example"),
]

# (days ago, hour of day, amount in cents, customer index, story)
PAYMENTS = [
    (13, 10, 12900, 0, "paid"),
    (12, 15, 4800, 1, "paid"),
    (11, 9, 31999, 2, "refund_partial"),
    (10, 13, 7450, 3, "paid"),
    (9, 17, 1999, 4, "refund_full"),
    (8, 11, 56000, 5, "manual_partial"),
    (7, 14, 2500, 0, "paid"),
    (6, 10, 1500, 2, "declined"),
    (5, 16, 8900, 1, "paid"),
    (4, 12, 24000, 3, "paid"),
    (3, 9, 6400, 4, "needs_3ds"),
    (2, 15, 15800, 5, "paid"),
    (1, 11, 8800, 0, "uncaptured"),
    (0, 0, 4200, 1, "paid"),  # minutes ago: still pending
]


def is_sandbox(merchant: Merchant) -> bool:
    return merchant.email.endswith("@" + SANDBOX_DOMAIN)


def create_demo_merchant(db: Session) -> Merchant:
    """A new sandbox merchant (no password: reachable only through the session it's given)."""
    merchant, _, _ = create_merchant(db, f"demo-{secrets.token_hex(4)}@{SANDBOX_DOMAIN}")
    seed(db, merchant.id)
    return merchant


def seed(db: Session, merchant_id: str) -> None:
    now = clock.now()
    start = now - timedelta(days=14)
    customers = []
    with clock.at(start):
        for name, email in CUSTOMERS:
            c = Customer(id=new_id("cus_"), merchant_id=merchant_id, name=name, email=email)
            db.add(c)
            customers.append(c)
        db.flush()
        for c in customers:
            emit(db, merchant_id, "customer.created", customer_json(c))

    actions = []  # (when, callable): replayed in time order
    for days, hour, amount, who, story in PAYMENTS:
        day = (now - timedelta(days=days)).replace(hour=hour, minute=12, second=0, microsecond=0)
        if days == 0:
            day = now - timedelta(minutes=2)  # still pending: the worker releases it later
        actions.append((day, _payment(db, merchant_id, customers[who].id, amount, story, day)))
    for when, act in sorted(actions, key=lambda a: a[0]):
        with clock.at(when):
            act()
    _settle_history(db, merchant_id, now)


def _payment(db, merchant_id, customer_id, amount, story, when):
    def run():
        manual = story in ("manual_partial", "uncaptured")
        method = {
            "declined": "pm_card_visa_chargeDeclined",
            "needs_3ds": "pm_card_authenticationRequired",
        }.get(story, "pm_card_visa")
        pi = create_pi(
            db,
            merchant_id,
            amount=amount,
            currency="usd",
            capture_method="manual" if manual else "automatic",
            customer_id=customer_id,
            return_url="https://shop.cleartill-demo.example/return",
            metadata={"order": f"#{1000 + amount % 997}"},
        )
        pm = resolve_payment_method(db, merchant_id, method)
        confirm_pi(db, pi, payment_method=pm)
        if story == "manual_partial":
            with clock.at(when + timedelta(hours=3)):
                capture_pi(db, pi, amount - 6000, None)  # one item went out of stock
        if story in ("refund_partial", "refund_full"):
            _release_due(db, merchant_id, when + timedelta(days=1))
            with clock.at(when + timedelta(days=1, hours=2)):
                create_refund(
                    db,
                    merchant_id,
                    charge_id=None,
                    payment_intent_id=pi.id,
                    amount=4000 if story == "refund_partial" else None,
                    reason="requested_by_customer",
                    request=None,
                )

    return run


def _release_due(db: Session, merchant_id: str, until) -> None:
    """Release this merchant's captured funds whose availability date has passed by `until`,
    each at its own availability moment (what the worker does for live traffic)."""
    due = db.scalars(
        select(BalanceTransaction)
        .where(
            BalanceTransaction.merchant_id == merchant_id,
            BalanceTransaction.type == "charge",
            BalanceTransaction.status == "pending",
            BalanceTransaction.available_on <= until,
        )
        .order_by(BalanceTransaction.available_on)
    ).all()
    for bt in due:
        with clock.at(bt.available_on):
            ledger.release(db, bt)


def _settle_history(db: Session, merchant_id: str, now) -> None:
    """Release everything due by now, then two payouts: one long paid, one in transit."""
    _release_due(db, merchant_id, now - timedelta(days=5))
    older = now - timedelta(days=5)
    with clock.at(older):
        first = available(db, merchant_id, "usd")
        if first > 0:
            payout = create_payout(db, merchant_id, int(first * 0.8), "usd")
    _release_due(db, merchant_id, now)
    if first > 0:
        arrived = max(payout.arrival_date, older + timedelta(minutes=5))
        if arrived <= now:
            with clock.at(arrived):
                if ledger.settle_payout(db, payout):
                    emit(db, merchant_id, "payout.paid", payout_json(payout))
    recent = now - timedelta(hours=1)
    with clock.at(recent):
        balance = available(db, merchant_id, "usd")
        if balance > 0:
            create_payout(db, merchant_id, int(balance * 0.5), "usd")
    db.flush()
