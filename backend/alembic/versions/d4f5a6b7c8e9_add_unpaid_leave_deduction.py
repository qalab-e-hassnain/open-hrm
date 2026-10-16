"""Add unpaid_leave_deduction to payrolls

Revision ID: d4f5a6b7c8e9
Revises: c3e4f5a6b7d8
Create Date: 2026-07-22 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'd4f5a6b7c8e9'
down_revision = 'c3e4f5a6b7d8'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'payrolls',
        sa.Column('unpaid_leave_deduction', sa.Float(), nullable=False, server_default='0'),
    )


def downgrade() -> None:
    op.drop_column('payrolls', 'unpaid_leave_deduction')
