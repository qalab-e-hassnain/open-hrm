"""Add is_historical to payrolls

Revision ID: b2d3f4a5c6e7
Revises: a1c2e3f4b5d6
Create Date: 2026-07-20 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b2d3f4a5c6e7'
down_revision = 'a1c2e3f4b5d6'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # server_default backfills existing rows to False; SQLAlchemy renders a
    # suitable literal for both SQLite and PostgreSQL.
    op.add_column(
        'payrolls',
        sa.Column('is_historical', sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column('payrolls', 'is_historical')
