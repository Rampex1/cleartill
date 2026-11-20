"""merchants and api_keys

Revision ID: 0001
Revises:
"""

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "merchants",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("seq", sa.BigInteger, sa.Identity(), nullable=False, unique=True),
        sa.Column("email", sa.Text, nullable=False, unique=True),
        sa.Column("password_hash", sa.Text),
        sa.Column("created", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "api_keys",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("seq", sa.BigInteger, sa.Identity(), nullable=False, unique=True),
        sa.Column("merchant_id", sa.Text, sa.ForeignKey("merchants.id"), nullable=False),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("key_hash", sa.Text, nullable=False, unique=True),
        sa.Column("last4", sa.Text, nullable=False),
        sa.Column("created", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("kind IN ('secret', 'publishable')", name="api_keys_kind"),
    )
    op.create_index("ix_api_keys_merchant_id", "api_keys", ["merchant_id"])


def downgrade() -> None:
    op.drop_table("api_keys")
    op.drop_table("merchants")
