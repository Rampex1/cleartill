"""charges: amount_captured can't exceed amount (defense in depth; capture_charge checks too)

Revision ID: 0015
Revises: 0014
"""

from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_check_constraint("charges_captured_le_amount", "charges", "amount_captured <= amount")


def downgrade() -> None:
    op.drop_constraint("charges_captured_le_amount", "charges", type_="check")
