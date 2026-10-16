"""Payroll sheet: runs, entries, audit log + employee base_salary

Revision ID: c3d7e91a4b28
Revises: 2f1a2b0f54b5
Create Date: 2026-07-15 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c3d7e91a4b28'
down_revision = '2f1a2b0f54b5'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Employee's current monthly salary. Payroll entries snapshot this at run
    # creation; existing users default to 0 until an admin sets it.
    op.add_column('users', sa.Column('base_salary', sa.Float(), nullable=True, server_default='0'))

    # One payroll sheet per month.
    op.create_table(
        'payroll_runs',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('month', sa.String(), nullable=False),  # "YYYY-MM"
        sa.Column('status', sa.Enum('DRAFT', 'FINALIZED', 'PAID', name='payrollrunstatusenum'), nullable=False),
        sa.Column('created_by', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('finalized_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['created_by'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_payroll_runs_month', 'payroll_runs', ['month'], unique=True)

    # One row per employee on a sheet. gross_salary/gross_total/net_payable are
    # deliberately NOT columns — they're computed by the ORM hybrid properties.
    op.create_table(
        'payroll_entries',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('payroll_run_id', sa.Integer(), nullable=False),
        sa.Column('employee_id', sa.Integer(), nullable=False),
        sa.Column('monthly_salary', sa.Float(), nullable=False, server_default='0'),
        sa.Column('increment', sa.Float(), nullable=False, server_default='0'),
        sa.Column('allowance_overtime', sa.Float(), nullable=False, server_default='0'),
        sa.Column('arrears', sa.Float(), nullable=False, server_default='0'),
        sa.Column('bonus', sa.Float(), nullable=False, server_default='0'),
        sa.Column('tax_deduction', sa.Float(), nullable=False, server_default='0'),
        sa.Column('other_deduction', sa.Float(), nullable=False, server_default='0'),
        sa.Column('notes', sa.String(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['payroll_run_id'], ['payroll_runs.id']),
        sa.ForeignKeyConstraint(['employee_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('payroll_run_id', 'employee_id', name='uq_payroll_entry_run_employee'),
    )
    op.create_index('ix_payroll_entries_payroll_run_id', 'payroll_entries', ['payroll_run_id'])
    op.create_index('ix_payroll_entries_employee_id', 'payroll_entries', ['employee_id'])

    # Audit trail. payroll_entry_id is nullable so run-level events
    # (finalize/reopen) can be logged against payroll_run_id instead; `note`
    # carries text that doesn't fit the float old/new columns.
    op.create_table(
        'payroll_audit_log',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('payroll_entry_id', sa.Integer(), nullable=True),
        sa.Column('payroll_run_id', sa.Integer(), nullable=True),
        sa.Column('field_changed', sa.String(), nullable=False),
        sa.Column('old_value', sa.Float(), nullable=True),
        sa.Column('new_value', sa.Float(), nullable=True),
        sa.Column('note', sa.String(), nullable=True),
        sa.Column('changed_by', sa.Integer(), nullable=False),
        sa.Column('changed_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['payroll_entry_id'], ['payroll_entries.id']),
        sa.ForeignKeyConstraint(['payroll_run_id'], ['payroll_runs.id']),
        sa.ForeignKeyConstraint(['changed_by'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_payroll_audit_log_payroll_entry_id', 'payroll_audit_log', ['payroll_entry_id'])
    op.create_index('ix_payroll_audit_log_payroll_run_id', 'payroll_audit_log', ['payroll_run_id'])


def downgrade() -> None:
    op.drop_index('ix_payroll_audit_log_payroll_run_id', table_name='payroll_audit_log')
    op.drop_index('ix_payroll_audit_log_payroll_entry_id', table_name='payroll_audit_log')
    op.drop_table('payroll_audit_log')
    op.drop_index('ix_payroll_entries_employee_id', table_name='payroll_entries')
    op.drop_index('ix_payroll_entries_payroll_run_id', table_name='payroll_entries')
    op.drop_table('payroll_entries')
    op.drop_index('ix_payroll_runs_month', table_name='payroll_runs')
    op.drop_table('payroll_runs')
    op.drop_column('users', 'base_salary')
