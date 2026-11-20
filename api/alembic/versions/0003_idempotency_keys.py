"""idempotency_keys

Revision ID: 0003
Revises: 0002
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "idempotency_keys",
        sa.Column("merchant_id", sa.Text, sa.ForeignKey("merchants.id"), primary_key=True),
        sa.Column("key", sa.Text, primary_key=True),
        sa.Column("request_hash", sa.Text, nullable=False),
        sa.Column("response_status", sa.Integer),
        sa.Column("response_body", JSONB),
        sa.Column("created", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_idempotency_keys_created", "idempotency_keys", ["created"])


def downgrade() -> None:
    op.drop_table("idempotency_keys")
