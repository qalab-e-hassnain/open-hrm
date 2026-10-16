"""payslip tax_is_manual flag

Revision ID: df4414ee3f4d
Revises: e1d11e1e87fd
Create Date: 2026-09-03 15:14:36.026212

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'df4414ee3f4d'
down_revision = 'e1d11e1e87fd'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """
    Marks a payslip's tax as set by a person rather than derived from the slab
    table, so a later recompute preserves it. Payroll-run figures are the main
    source: an employee contributing to a pension scheme has income exempt from
    salary tax, which the slab engine cannot see, so the correct figure is
    lower than the computed one.
    """
    op.add_column(
        "payrolls",
        sa.Column("tax_is_manual", sa.Boolean(), nullable=False, server_default="false"),
    )
    # Backfilled rows already trusted their entered tax; keep that true now
    # that the behaviour is expressed by this flag instead.
    op.execute("UPDATE payrolls SET tax_is_manual = true WHERE is_historical = true")


def downgrade() -> None:
    op.drop_column("payrolls", "tax_is_manual")

