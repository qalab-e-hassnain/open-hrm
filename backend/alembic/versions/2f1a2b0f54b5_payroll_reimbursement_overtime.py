"""Payroll: reimbursement + overtime line items, tax engine, overtime records

Revision ID: 2f1a2b0f54b5
Revises: be19fca3021a
Create Date: 2026-07-13 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '2f1a2b0f54b5'
down_revision = 'be19fca3021a'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # New earning line-item aggregates on the payslip (default 0 for existing rows).
    op.add_column('payrolls', sa.Column('reimbursement_total', sa.Float(), nullable=True, server_default='0'))
    op.add_column('payrolls', sa.Column('overtime_pay', sa.Float(), nullable=True, server_default='0'))

    # Link an approved reimbursement to the payroll that absorbed it.
    # Plain Integer (no ALTER-added FK) so this works on SQLite; the ORM model
    # still declares the ForeignKey for relationship resolution.
    op.add_column('reimbursements', sa.Column('payroll_id', sa.Integer(), nullable=True))

    # Overtime approval records — the admin gate for overtime pay.
    op.create_table(
        'overtime_records',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('employee_id', sa.Integer(), nullable=False),
        sa.Column('month', sa.Integer(), nullable=False),
        sa.Column('year', sa.Integer(), nullable=False),
        sa.Column('overtime_hours', sa.Float(), nullable=False, server_default='0'),
        sa.Column('overtime_pay', sa.Float(), nullable=True, server_default='0'),
        sa.Column('status', sa.Enum('PENDING', 'APPROVED', 'REJECTED', name='overtimestatusenum'), nullable=False),
        sa.Column('approved_by', sa.Integer(), nullable=True),
        sa.Column('approved_at', sa.DateTime(), nullable=True),
        sa.Column('payroll_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['employee_id'], ['users.id']),
        sa.ForeignKeyConstraint(['approved_by'], ['users.id']),
        sa.ForeignKeyConstraint(['payroll_id'], ['payrolls.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('employee_id', 'month', 'year', name='uq_overtime_employee_period'),
    )
    op.create_index('ix_overtime_records_employee_id', 'overtime_records', ['employee_id'])


def downgrade() -> None:
    op.drop_index('ix_overtime_records_employee_id', table_name='overtime_records')
    op.drop_table('overtime_records')
    op.drop_column('reimbursements', 'payroll_id')
    op.drop_column('payrolls', 'overtime_pay')
    op.drop_column('payrolls', 'reimbursement_total')
