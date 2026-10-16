"""add leave reminder markers

Revision ID: ddbcdfc0a62e
Revises: 0659788aa2b4
Create Date: 2026-08-12 15:48:22.563265

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'ddbcdfc0a62e'
down_revision = '0659788aa2b4'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('leaves', sa.Column('overdue_reminder_sent_at', sa.DateTime(), nullable=True))
    op.add_column('leaves', sa.Column('upcoming_reminder_sent_at', sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column('leaves', 'upcoming_reminder_sent_at')
    op.drop_column('leaves', 'overdue_reminder_sent_at')

