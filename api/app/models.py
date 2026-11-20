from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Identity, Text
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
