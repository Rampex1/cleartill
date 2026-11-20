"""housekeeping indexes: due releases and expired sessions

Revision ID: 0013
Revises: 0012
"""

from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX balance_transactions_due_release ON balance_transactions (available_on) "
        "WHERE status = 'pending' AND type = 'charge'"
    )
    op.execute("CREATE INDEX dashboard_sessions_expires_at ON dashboard_sessions (expires_at)")


def downgrade() -> None:
    op.execute("DROP INDEX dashboard_sessions_expires_at")
    op.execute("DROP INDEX balance_transactions_due_release")
