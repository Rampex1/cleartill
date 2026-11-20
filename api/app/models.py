from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
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
    __table_args__ = (CheckConstraint("amount <> 0", name="journal_entries_nonzero"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    transaction_id: Mapped[str] = mapped_column(ForeignKey("journal_transactions.id"), index=True)
    account_id: Mapped[str] = mapped_column(ForeignKey("ledger_accounts.id"), index=True)
    amount: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(Text)


class BalanceTransaction(Base):
    __tablename__ = "balance_transactions"
    __table_args__ = (Index("ix_balance_transactions_merchant_seq", "merchant_id", "seq"),)
    object_name = "balance transaction"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    seq: Mapped[int] = mapped_column(BigInteger, Identity(), unique=True)
    merchant_id: Mapped[str] = mapped_column(ForeignKey("merchants.id", ondelete="CASCADE"))
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
