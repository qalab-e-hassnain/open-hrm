"""inv05: stock requests and cycle-count sessions

Phases 4 and 5. Requests are an engineer asking, a lead approving and the
custodian issuing, with the movement that satisfied the request stored on it
rather than inferred from timestamps afterwards.

Audit sessions snapshot what is expected at the moment the count starts, so the
expected figures are the ones that were true when the custodian walked to the
rack rather than ones that moved while they were counting.

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-09-20 09:40:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'e5f6a7b8c9d0'
down_revision = 'd4e5f6a7b8c9'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'inv_requests',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('requester_id', sa.Integer(), nullable=False),
        sa.Column('item_id', sa.Integer(), nullable=True),
        sa.Column('category_id', sa.Integer(), nullable=True),
        sa.Column('qty', sa.Float(), nullable=False, server_default='1'),
        sa.Column('project_id', sa.Integer(), nullable=True),
        sa.Column('purpose', sa.Text(), nullable=False),
        sa.Column('needed_by', sa.Date(), nullable=True),
        sa.Column('status', sa.String(), nullable=False, server_default='pending'),
        sa.Column('approver_id', sa.Integer(), nullable=True),
        sa.Column('approved_at', sa.DateTime(), nullable=True),
        sa.Column('rejection_reason', sa.Text(), nullable=True),
        sa.Column('issued_movement_id', sa.Integer(), nullable=True),
        sa.Column('reminded_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False,
                  server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.ForeignKeyConstraint(['requester_id'], ['users.id']),
        sa.ForeignKeyConstraint(['approver_id'], ['users.id']),
        sa.ForeignKeyConstraint(['item_id'], ['inv_items.id']),
        sa.ForeignKeyConstraint(['category_id'], ['inv_categories.id']),
        sa.ForeignKeyConstraint(['issued_movement_id'], ['inv_movements.id']),
        sa.PrimaryKeyConstraint('id'),
        # A request names an item or a category. Neither is not a request.
        sa.CheckConstraint('item_id IS NOT NULL OR category_id IS NOT NULL',
                           name='ck_inv_requests_target'),
    )
    op.create_index('ix_inv_requests_id', 'inv_requests', ['id'])
    op.create_index('ix_inv_requests_requester', 'inv_requests', ['requester_id'])
    op.create_index('ix_inv_requests_status', 'inv_requests', ['status'])
    op.create_index('ix_inv_requests_item_id', 'inv_requests', ['item_id'])
    op.create_index('ix_inv_requests_project_id', 'inv_requests', ['project_id'])

    op.create_table(
        'inv_audit_sessions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('scope_location_id', sa.Integer(), nullable=True),
        sa.Column('scope_category_id', sa.Integer(), nullable=True),
        sa.Column('label', sa.String(), nullable=True),
        sa.Column('status', sa.String(), nullable=False, server_default='open'),
        sa.Column('opened_by', sa.Integer(), nullable=False),
        sa.Column('opened_at', sa.DateTime(), nullable=False,
                  server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('closed_at', sa.DateTime(), nullable=True),
        sa.Column('variance_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('variance_value', sa.Float(), nullable=False, server_default='0'),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(['scope_location_id'], ['inv_locations.id']),
        sa.ForeignKeyConstraint(['scope_category_id'], ['inv_categories.id']),
        sa.ForeignKeyConstraint(['opened_by'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.CheckConstraint(
            'scope_location_id IS NOT NULL OR scope_category_id IS NOT NULL',
            name='ck_inv_audit_sessions_scope'),
    )
    op.create_index('ix_inv_audit_sessions_id', 'inv_audit_sessions', ['id'])
    op.create_index('ix_inv_audit_sessions_status', 'inv_audit_sessions', ['status'])

    op.create_table(
        'inv_audit_lines',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('session_id', sa.Integer(), nullable=False),
        sa.Column('item_id', sa.Integer(), nullable=False),
        sa.Column('expected_qty', sa.Float(), nullable=False, server_default='0'),
        sa.Column('counted_qty', sa.Float(), nullable=True),
        sa.Column('variance', sa.Float(), nullable=False, server_default='0'),
        sa.Column('variance_value', sa.Float(), nullable=False, server_default='0'),
        sa.Column('outcome', sa.String(), nullable=True),
        sa.Column('note', sa.Text(), nullable=True),
        sa.Column('scanned_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['session_id'], ['inv_audit_sessions.id']),
        sa.ForeignKeyConstraint(['item_id'], ['inv_items.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('session_id', 'item_id', name='uq_inv_audit_line'),
    )
    op.create_index('ix_inv_audit_lines_id', 'inv_audit_lines', ['id'])
    op.create_index('ix_inv_audit_lines_session', 'inv_audit_lines', ['session_id'])
    op.create_index('ix_inv_audit_lines_item', 'inv_audit_lines', ['item_id'])


def downgrade() -> None:
    op.drop_table('inv_audit_lines')
    op.drop_table('inv_audit_sessions')
    op.drop_table('inv_requests')
