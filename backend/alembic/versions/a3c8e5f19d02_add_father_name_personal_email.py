"""Add father_name and personal_email to users

Revision ID: a3c8e5f19d02
Revises: f8c1a4d70b21
Create Date: 2026-08-05 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a3c8e5f19d02'
down_revision = 'f8c1a4d70b21'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Both were already editable on the frontend profile form but had no
    # backing column, so FastAPI silently dropped them on every save.
    op.add_column('users', sa.Column('father_name', sa.String(), nullable=True))
    op.add_column('users', sa.Column('personal_email', sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column('users', 'personal_email')
    op.drop_column('users', 'father_name')
