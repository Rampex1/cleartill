from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Identity,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    false,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app import clock
from app.db import Base


class Merchant(Base):
    __tablename__ = "merchants"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    seq: Mapped[int] = mapped_column(BigInteger, Identity(), unique=True)
    email: Mapped[str] = mapped_column(Text, unique=True)
    password_hash: Mapped[str | None] = mapped_column(Text)
    created: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: clock.now())


class ApiKey(Base):
    __tablename__ = "api_keys"
    __table_args__ = (CheckConstraint("kind IN ('secret', 'publishable')", name="api_keys_kind"),)

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    seq: Mapped[int] = mapped_column(BigInteger, Identity(), unique=True)
    merchant_id: Mapped[str] = mapped_column(ForeignKey("merchants.id"), index=True)
    kind: Mapped[str] = mapped_column(Text)
    key_hash: Mapped[str] = mapped_column(Text, unique=True)
    last4: Mapped[str] = mapped_column(Text)
    created: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: clock.now())
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Customer(Base):
    __tablename__ = "customers"
    __table_args__ = (Index("ix_customers_merchant_seq", "merchant_id", "seq"),)
    object_name = "customer"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    seq: Mapped[int] = mapped_column(BigInteger, Identity(), unique=True)
    merchant_id: Mapped[str] = mapped_column(ForeignKey("merchants.id"))
    email: Mapped[str | None] = mapped_column(Text)
    name: Mapped[str | None] = mapped_column(Text)
    # `metadata` is reserved on declarative classes; the column is still called metadata.
    metadata_: Mapped[dict] = mapped_column(
        "metadata", JSONB, default=dict, server_default=text("'{}'::jsonb")
    )
    deleted: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    created: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: clock.now())


class IdempotencyKey(Base):
    __tablename__ = "idempotency_keys"
    __table_args__ = (Index("ix_idempotency_keys_created", "created"),)

    merchant_id: Mapped[str] = mapped_column(ForeignKey("merchants.id"), primary_key=True)
    key: Mapped[str] = mapped_column(Text, primary_key=True)
    request_hash: Mapped[str] = mapped_column(Text)
    response_status: Mapped[int | None] = mapped_column(Integer)
    response_body: Mapped[dict | None] = mapped_column(JSONB)
    created: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: clock.now())


class DashboardSession(Base):
    __tablename__ = "dashboard_sessions"

    token_hash: Mapped[str] = mapped_column(Text, primary_key=True)
    merchant_id: Mapped[str] = mapped_column(ForeignKey("merchants.id"), index=True)
    created: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: clock.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class LedgerAccount(Base):
    __tablename__ = "ledger_accounts"
    __table_args__ = (
        UniqueConstraint("merchant_id", "kind", "currency", name="uq_ledger_accounts_merchant"),
        # NULLs are distinct in a plain UNIQUE, so platform accounts need their own index.
        Index(
            "ux_ledger_accounts_platform",
            "kind",
            "currency",
            unique=True,
            postgresql_where=text("merchant_id IS NULL"),
        ),
        CheckConstraint(
            "kind IN ('merchant_pending', 'merchant_available', "
            "'card_network_clearing', 'platform_fees')",
            name="ledger_accounts_kind",
        ),
        # Target of journal_entries' (account_id, currency) FK: an entry's currency must be
        # its account's.
        UniqueConstraint("id", "currency", name="uq_ledger_accounts_id_currency"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    merchant_id: Mapped[str | None] = mapped_column(ForeignKey("merchants.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(Text)
    currency: Mapped[str] = mapped_column(Text)


class JournalTransaction(Base):
    __tablename__ = "journal_transactions"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    type: Mapped[str] = mapped_column(Text)
    source_id: Mapped[str] = mapped_column(Text)
    created: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: clock.now())


class JournalEntry(Base):
    __tablename__ = "journal_entries"
    __table_args__ = (
        CheckConstraint("amount <> 0", name="journal_entries_nonzero"),
        ForeignKeyConstraint(
            ["account_id", "currency"],
            ["ledger_accounts.id", "ledger_accounts.currency"],
            name="fk_journal_entries_account_currency",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    transaction_id: Mapped[str] = mapped_column(ForeignKey("journal_transactions.id"), index=True)
    account_id: Mapped[str] = mapped_column(Text, index=True)
    amount: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(Text)


class BalanceTransaction(Base):
    __tablename__ = "balance_transactions"
    __table_args__ = (Index("ix_balance_transactions_merchant_seq", "merchant_id", "seq"),)
    object_name = "balance transaction"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    seq: Mapped[int] = mapped_column(BigInteger, Identity(), unique=True)
    # No CASCADE: a merchant with money history can't be deleted.
    merchant_id: Mapped[str] = mapped_column(ForeignKey("merchants.id"))
    type: Mapped[str] = mapped_column(Text)
    amount: Mapped[int] = mapped_column(BigInteger)
    fee: Mapped[int] = mapped_column(BigInteger)
    net: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    available_on: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    source_id: Mapped[str] = mapped_column(Text)
    journal_transaction_id: Mapped[str | None] = mapped_column(
        ForeignKey("journal_transactions.id")
    )
    created: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: clock.now())


class Event(Base):
    __tablename__ = "events"
    __table_args__ = (Index("ix_events_merchant_seq", "merchant_id", "seq"),)
    object_name = "event"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    seq: Mapped[int] = mapped_column(BigInteger, Identity(), unique=True)
    merchant_id: Mapped[str] = mapped_column(ForeignKey("merchants.id", ondelete="CASCADE"))
    type: Mapped[str] = mapped_column(Text)
    data: Mapped[dict] = mapped_column(JSONB)  # {"object": <snapshot>}; never updated
    request: Mapped[dict] = mapped_column(JSONB)  # {"id", "idempotency_key"}
    created: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: clock.now())


class PaymentMethod(Base):
    """A tokenized card. Never holds the card number or CVC."""

    __tablename__ = "payment_methods"
    __table_args__ = (
        Index("ix_payment_methods_merchant_seq", "merchant_id", "seq"),
        CheckConstraint(
            "sim_outcome IN ('success', 'generic_decline', 'insufficient_funds', "
            "'expired_card', 'incorrect_cvc', 'processing_error', 'requires_3ds')",
            name="payment_methods_sim_outcome",
        ),
    )
    object_name = "payment method"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    seq: Mapped[int] = mapped_column(BigInteger, Identity(), unique=True)
    merchant_id: Mapped[str] = mapped_column(ForeignKey("merchants.id", ondelete="CASCADE"))
    brand: Mapped[str] = mapped_column(Text)
    last4: Mapped[str] = mapped_column(Text)
    exp_month: Mapped[int] = mapped_column(Integer)
    exp_year: Mapped[int] = mapped_column(Integer)
    fingerprint: Mapped[str] = mapped_column(Text)
    sim_outcome: Mapped[str] = mapped_column(Text)
    created: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: clock.now())


PI_STATUSES = (
    "requires_payment_method",
    "requires_confirmation",
    "requires_action",
    "processing",
    "requires_capture",
    "canceled",
    "succeeded",
)


class PaymentIntent(Base):
    __tablename__ = "payment_intents"
    __table_args__ = (
        Index("ix_payment_intents_merchant_seq", "merchant_id", "seq"),
        CheckConstraint(
            "status IN (" + ", ".join(f"'{s}'" for s in PI_STATUSES) + ")",
            name="payment_intents_status",
        ),
        CheckConstraint(
            "capture_method IN ('automatic', 'automatic_async', 'manual')",
            name="payment_intents_capture_method",
        ),
    )
    object_name = "payment intent"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    seq: Mapped[int] = mapped_column(BigInteger, Identity(), unique=True)
    merchant_id: Mapped[str] = mapped_column(ForeignKey("merchants.id", ondelete="CASCADE"))
    amount: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    capture_method: Mapped[str] = mapped_column(Text)
    amount_capturable: Mapped[int] = mapped_column(BigInteger, default=0)
    amount_received: Mapped[int] = mapped_column(BigInteger, default=0)
    customer_id: Mapped[str | None] = mapped_column(ForeignKey("customers.id"))
    payment_method_id: Mapped[str | None] = mapped_column(ForeignKey("payment_methods.id"))
    latest_charge_id: Mapped[str | None] = mapped_column(Text)  # FK added with charges (M9b)
    client_secret: Mapped[str] = mapped_column(Text)
    return_url: Mapped[str | None] = mapped_column(Text)
    last_payment_error: Mapped[dict | None] = mapped_column(JSONB)
    next_action: Mapped[dict | None] = mapped_column(JSONB)
    cancellation_reason: Mapped[str | None] = mapped_column(Text)
    # Set for PIs that a checkout session owns (M17 adds the FK and blocks /v1 mutations).
    checkout_session_id: Mapped[str | None] = mapped_column(Text)
    metadata_: Mapped[dict] = mapped_column(
        "metadata", JSONB, default=dict, server_default=text("'{}'::jsonb")
    )
    created: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: clock.now())


class Charge(Base):
    __tablename__ = "charges"
    __table_args__ = (
        Index("ix_charges_merchant_seq", "merchant_id", "seq"),
        CheckConstraint("status IN ('succeeded', 'failed')", name="charges_status"),
        CheckConstraint(
            "amount_captured >= 0 AND amount_refunded >= 0 AND amount_refunded <= amount_captured",
            name="charges_amounts",
        ),
    )
    object_name = "charge"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    seq: Mapped[int] = mapped_column(BigInteger, Identity(), unique=True)
    merchant_id: Mapped[str] = mapped_column(ForeignKey("merchants.id", ondelete="CASCADE"))
    payment_intent_id: Mapped[str] = mapped_column(ForeignKey("payment_intents.id"), index=True)
    payment_method_id: Mapped[str] = mapped_column(ForeignKey("payment_methods.id"))
    amount: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(Text)
    amount_captured: Mapped[int] = mapped_column(BigInteger, default=0)
    amount_refunded: Mapped[int] = mapped_column(BigInteger, default=0)
    captured: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(Text)
    failure_code: Mapped[str | None] = mapped_column(Text)
    failure_message: Mapped[str | None] = mapped_column(Text)
    authorized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    balance_transaction_id: Mapped[str | None] = mapped_column(
        ForeignKey("balance_transactions.id")
    )
    created: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: clock.now())
