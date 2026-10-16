"""Add arrears to payrolls

Revision ID: c3e4f5a6b7d8
Revises: b2d3f4a5c6e7
Create Date: 2026-07-21 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c3e4f5a6b7d8'
down_revision = 'b2d3f4a5c6e7'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'payrolls',
        sa.Column('arrears', sa.Float(), nullable=False, server_default='0'),
    )


def downgrade() -> None:
    op.drop_column('payrolls', 'arrears')
