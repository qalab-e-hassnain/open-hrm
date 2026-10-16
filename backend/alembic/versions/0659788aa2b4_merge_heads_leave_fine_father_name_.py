"""merge heads: leave fine + father name/personal email

Revision ID: 0659788aa2b4
Revises: a3c8e5f19d02, a9d4e6f2c8b1
Create Date: 2026-08-07 17:07:12.900782

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0659788aa2b4'
down_revision = ('a3c8e5f19d02', 'a9d4e6f2c8b1')
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass

