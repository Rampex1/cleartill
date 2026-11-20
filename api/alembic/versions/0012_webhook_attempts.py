"""webhook_attempts

Revision ID: 0012
Revises: 0011
"""

import sqlalchemy as sa
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "webhook_attempts",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column(
            "merchant_id",
            sa.Text,
            sa.ForeignKey("merchants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("delivery_id", sa.Text, sa.ForeignKey("webhook_deliveries.id"), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status_code", sa.Integer),
        sa.Column("error", sa.Text),
        sa.Column("duration_ms", sa.Integer, nullable=False),
        sa.Column("manual", sa.Boolean, nullable=False),
    )
    op.create_index("ix_webhook_attempts_delivery_id", "webhook_attempts", ["delivery_id"])


def downgrade() -> None:
    op.drop_table("webhook_attempts")
