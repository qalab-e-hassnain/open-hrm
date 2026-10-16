"""Add gender to users

Revision ID: be19fca3021a
Revises: bb1339c062bf
Create Date: 2026-07-10 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'be19fca3021a'
down_revision = 'bb1339c062bf'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Nullable: existing users predate this field and have no value yet.
    gender_enum = sa.Enum('MALE', 'FEMALE', 'OTHER', name='genderenum')
    if op.get_bind().dialect.name == 'postgresql':
        gender_enum.create(op.get_bind(), checkfirst=True)
    op.add_column('users', sa.Column('gender', gender_enum, nullable=True))


def downgrade() -> None:
    op.drop_column('users', 'gender')
    if op.get_bind().dialect.name == 'postgresql':
        sa.Enum('MALE', 'FEMALE', 'OTHER', name='genderenum').drop(
            op.get_bind(), checkfirst=True
        )
