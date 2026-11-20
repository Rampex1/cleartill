"""dashboard_sessions

Revision ID: 0004
Revises: 0003
"""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "dashboard_sessions",
        sa.Column("token_hash", sa.Text, primary_key=True),
        sa.Column("merchant_id", sa.Text, sa.ForeignKey("merchants.id"), nullable=False),
        sa.Column("created", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_dashboard_sessions_merchant_id", "dashboard_sessions", ["merchant_id"])


def downgrade() -> None:
    op.drop_table("dashboard_sessions")
