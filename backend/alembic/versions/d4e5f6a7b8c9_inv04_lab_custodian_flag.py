"""inv04: the lab custodian flag

The design asks for four roles and no new permission system. This repository's
roles are employee / manager / admin, and HR is already a flag rather than a
role for a stated reason — "so it can be revoked". The custodian is the same
kind of thing, so it gets the same treatment.

An admin reaches everything a custodian does without carrying the flag; see
deps.is_custodian.

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-09-20 09:30:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'd4e5f6a7b8c9'
down_revision = 'c3d4e5f6a7b8'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('users', sa.Column('is_lab_custodian', sa.Boolean(),
                                     nullable=False, server_default='false'))


def downgrade() -> None:
    op.drop_column('users', 'is_lab_custodian')
