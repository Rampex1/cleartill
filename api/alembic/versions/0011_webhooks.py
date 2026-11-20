"""webhook endpoints and deliveries

Revision ID: 0011
Revises: 0010
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "webhook_endpoints",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("seq", sa.BigInteger, sa.Identity(), nullable=False, unique=True),
        sa.Column(
            "merchant_id",
            sa.Text,
            sa.ForeignKey("merchants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("url", sa.Text, nullable=False),
        sa.Column("enabled_events", ARRAY(sa.Text), nullable=False),
        sa.Column("description", sa.Text),
        sa.Column("secret", sa.Text, nullable=False),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("deleted", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("created", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status IN ('enabled', 'disabled')", name="webhook_endpoints_status"),
    )
    op.create_index(
        "ix_webhook_endpoints_merchant_seq", "webhook_endpoints", ["merchant_id", "seq"]
    )
    op.create_table(
        "webhook_deliveries",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column(
            "merchant_id",
            sa.Text,
            sa.ForeignKey("merchants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "event_id", sa.Text, sa.ForeignKey("events.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "endpoint_id",
            sa.Text,
            sa.ForeignKey("webhook_endpoints.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("attempts", sa.Integer, nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("locked_until", sa.DateTime(timezone=True)),
        sa.Column("claim_id", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("event_id", "endpoint_id", name="uq_webhook_deliveries_event_endpoint"),
        sa.CheckConstraint(
            "status IN ('pending', 'succeeded', 'failed')", name="webhook_deliveries_status"
        ),
    )
    op.create_index(
        "ix_webhook_deliveries_due", "webhook_deliveries", ["status", "next_attempt_at"]
    )
    op.create_index("ix_webhook_deliveries_endpoint_id", "webhook_deliveries", ["endpoint_id"])


def downgrade() -> None:
    op.drop_table("webhook_deliveries")
    op.drop_table("webhook_endpoints")
