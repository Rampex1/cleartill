"""payment_methods

Revision ID: 0007
Revises: 0006
"""

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "payment_methods",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("seq", sa.BigInteger, sa.Identity(), nullable=False, unique=True),
        sa.Column(
            "merchant_id",
            sa.Text,
            sa.ForeignKey("merchants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("brand", sa.Text, nullable=False),
        sa.Column("last4", sa.Text, nullable=False),
        sa.Column("exp_month", sa.Integer, nullable=False),
        sa.Column("exp_year", sa.Integer, nullable=False),
        sa.Column("fingerprint", sa.Text, nullable=False),
        sa.Column("sim_outcome", sa.Text, nullable=False),
        sa.Column("created", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "sim_outcome IN ('success', 'generic_decline', 'insufficient_funds', "
            "'expired_card', 'incorrect_cvc', 'processing_error', 'requires_3ds')",
            name="payment_methods_sim_outcome",
        ),
    )
    op.create_index("ix_payment_methods_merchant_seq", "payment_methods", ["merchant_id", "seq"])


def downgrade() -> None:
    op.drop_table("payment_methods")
