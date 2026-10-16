"""annual leave allowance 24 to 14

Revision ID: e1d11e1e87fd
Revises: 71a1a51af38e
Create Date: 2026-09-03 13:09:12.890114

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'e1d11e1e87fd'
down_revision = '71a1a51af38e'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """
    Revised leave policy, effective 01 January 2026: annual allowance cut from
    24 to 14 days.

    Only rows still carrying the old default are touched, so an allowance an
    admin has deliberately set for one employee is preserved. `annual_used` is
    untouched: the days were genuinely taken, and no one has used more than 14
    this year, so nobody is pushed into a negative balance.
    """
    op.execute("UPDATE leave_balances SET annual_total = 14.0 WHERE annual_total = 24.0")
    op.alter_column("leave_balances", "annual_total", server_default="14.0")


def downgrade() -> None:
    op.execute("UPDATE leave_balances SET annual_total = 24.0 WHERE annual_total = 14.0")
    op.alter_column("leave_balances", "annual_total", server_default="24.0")

