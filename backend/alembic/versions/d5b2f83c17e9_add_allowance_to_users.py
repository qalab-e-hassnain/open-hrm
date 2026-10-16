"""Add recurring monthly allowance to users

Revision ID: d5b2f83c17e9
Revises: c3d7e91a4b28
Create Date: 2026-07-15 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd5b2f83c17e9'
down_revision = 'c3d7e91a4b28'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Recurring monthly allowance, used to prefill payslip generation.
    # Existing users default to 0 until an admin sets it.
    op.add_column('users', sa.Column('allowance', sa.Float(), nullable=True, server_default='0'))


def downgrade() -> None:
    op.drop_column('users', 'allowance')
