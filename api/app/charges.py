"""Charges: one per confirm attempt that reaches the network (design §5.7)."""

from sqlalchemy.orm import Session

from app import ledger
from app.models import Charge, PaymentMethod


def capture_charge(db: Session, charge: Charge, amount: int) -> None:
    """Post the captured amount to the ledger and mark the charge captured."""
    bt = ledger.post_capture(db, charge.merchant_id, charge.id, amount, charge.currency)
    charge.captured = True
    charge.amount_captured = amount
    charge.balance_transaction_id = bt.id
    db.flush()


def charge_json(db: Session, charge: Charge) -> dict:
    pm = db.get(PaymentMethod, charge.payment_method_id)
    return {
        "id": charge.id,
        "object": "charge",
        "amount": charge.amount,
        "amount_captured": charge.amount_captured,
        "amount_refunded": charge.amount_refunded,
        "captured": charge.captured,
        "refunded": charge.amount_captured > 0 and charge.amount_refunded == charge.amount_captured,
        "status": charge.status,
        "failure_code": charge.failure_code,
        "failure_message": charge.failure_message,
        "payment_intent": charge.payment_intent_id,
        "payment_method": charge.payment_method_id,
        "payment_method_details": {
            "card": {
                "brand": pm.brand,
                "last4": pm.last4,
                "exp_month": pm.exp_month,
                "exp_year": pm.exp_year,
            }
        },
        "balance_transaction": charge.balance_transaction_id,
        "currency": charge.currency,
        "created": int(charge.created.timestamp()),
        "livemode": False,
    }
