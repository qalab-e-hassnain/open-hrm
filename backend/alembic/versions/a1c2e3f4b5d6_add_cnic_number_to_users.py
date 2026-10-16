"""Add cnic_number to users

Revision ID: a1c2e3f4b5d6
Revises: d5b2f83c17e9
Create Date: 2026-07-20 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a1c2e3f4b5d6'
down_revision = 'd5b2f83c17e9'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('users', sa.Column('cnic_number', sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column('users', 'cnic_number')
