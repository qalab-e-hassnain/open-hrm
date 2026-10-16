"""add leave acting_admin_id

Revision ID: d72bd9be426f
Revises: ddbcdfc0a62e
Create Date: 2026-08-12 20:36:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd72bd9be426f'
down_revision = 'ddbcdfc0a62e'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('leaves', sa.Column('acting_admin_id', sa.Integer(), nullable=True))
    op.create_foreign_key(
        'fk_leaves_acting_admin_id_users', 'leaves', 'users',
        ['acting_admin_id'], ['id'],
    )


def downgrade() -> None:
    op.drop_constraint('fk_leaves_acting_admin_id_users', 'leaves', type_='foreignkey')
    op.drop_column('leaves', 'acting_admin_id')
