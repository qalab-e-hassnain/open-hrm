"""Track refresh tokens so a session can be ended

Refresh tokens were stateless: signed, good for a week, and unknown to the
server. Signing out dropped them client-side and nothing was revoked, so the
token kept working; and because nothing recorded which token was current,
rotating on refresh left the old one valid too.

This table gives every issued token a row, keyed by its `jti`, grouped into a
family per sign-in. Rotation marks the spent link, and presenting a spent link
revokes the whole family — see app/models/refresh_token.py.

Reversible. Downgrading drops the table, which signs everybody out of their
remembered devices (their next refresh has nowhere to look itself up) but
loses nothing else; access tokens keep working until they expire.

Revision ID: c9d0e1f2a3b4
Revises: b8c9d0e1f2a3
Create Date: 2026-10-16
"""
from alembic import op
import sqlalchemy as sa

revision = 'c9d0e1f2a3b4'
down_revision = 'b8c9d0e1f2a3'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'refresh_tokens',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('jti', sa.String(length=64), nullable=False),
        sa.Column('family_id', sa.String(length=64), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('issued_at', sa.DateTime(), nullable=False),
        sa.Column('expires_at', sa.DateTime(), nullable=False),
        sa.Column('family_started_at', sa.DateTime(), nullable=False),
        sa.Column('revoked_at', sa.DateTime(), nullable=True),
        sa.Column('revoked_reason', sa.String(length=40), nullable=True),
        sa.Column('replaced_by', sa.String(length=64), nullable=True),
        sa.Column('user_agent', sa.String(length=256), nullable=True),
        sa.Column('ip_address', sa.String(length=64), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_refresh_tokens_id'), 'refresh_tokens', ['id'])
    # Unique: a repeated jti would make "which token is this?" ambiguous at
    # exactly the moment the answer decides whether to end somebody's session.
    op.create_index(op.f('ix_refresh_tokens_jti'), 'refresh_tokens', ['jti'],
                    unique=True)
    op.create_index(op.f('ix_refresh_tokens_family_id'), 'refresh_tokens',
                    ['family_id'])
    op.create_index(op.f('ix_refresh_tokens_user_id'), 'refresh_tokens',
                    ['user_id'])
    op.create_index('ix_refresh_tokens_family_active', 'refresh_tokens',
                    ['family_id', 'revoked_at'])
    op.create_index('ix_refresh_tokens_expires_at', 'refresh_tokens',
                    ['expires_at'])


def downgrade():
    op.drop_index('ix_refresh_tokens_expires_at', table_name='refresh_tokens')
    op.drop_index('ix_refresh_tokens_family_active', table_name='refresh_tokens')
    op.drop_index(op.f('ix_refresh_tokens_user_id'), table_name='refresh_tokens')
    op.drop_index(op.f('ix_refresh_tokens_family_id'), table_name='refresh_tokens')
    op.drop_index(op.f('ix_refresh_tokens_jti'), table_name='refresh_tokens')
    op.drop_index(op.f('ix_refresh_tokens_id'), table_name='refresh_tokens')
    op.drop_table('refresh_tokens')
