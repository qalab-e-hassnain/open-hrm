"""payslip: allowance/deduction components, identity, revisions, disputes

Revision ID: e7a1c9d2b4f3
Revises: 721951e2b8c2
Create Date: 2026-07-29

Adds everything the payslip document needs beyond the aggregate figures:
  * allowance + deduction line items (they sum to the existing
    `allowances` / `deductions` totals, which stay authoritative)
  * payment method / reference
  * payslip number, verification code, version and revision chain
  * the payslip_disputes table backing "Report an issue"

Existing rows are backfilled so their totals still itemise: the whole
allowance total lands on `other_allowance` and the whole deduction total on
`other_deduction`, which keeps every historical payslip reconciling.
"""
from alembic import op
import sqlalchemy as sa


revision = 'e7a1c9d2b4f3'
down_revision = '721951e2b8c2'
branch_labels = None
depends_on = None


# The status column was created with the enum MEMBER NAMES, not the values —
# see fa16257d4801 (`sa.Enum('DRAFT', 'PROCESSED', 'PAID', ...)`) — so the new
# members have to be added in the same uppercase form.
NEW_STATUSES = ('PROCESSING', 'CORRECTED', 'CANCELLED')

MONEY = dict(nullable=False, server_default='0')


def upgrade() -> None:
    # --- allowance itemisation ---
    op.add_column('payrolls', sa.Column('house_allowance', sa.Float(), **MONEY))
    op.add_column('payrolls', sa.Column('transport_allowance', sa.Float(), **MONEY))
    op.add_column('payrolls', sa.Column('medical_allowance', sa.Float(), **MONEY))
    op.add_column('payrolls', sa.Column('other_allowance', sa.Float(), **MONEY))
    op.add_column('payrolls', sa.Column('other_allowance_note', sa.String(), nullable=True))

    # --- deduction itemisation ---
    op.add_column('payrolls', sa.Column('provident_fund', sa.Float(), **MONEY))
    op.add_column('payrolls', sa.Column('social_security', sa.Float(), **MONEY))
    op.add_column('payrolls', sa.Column('eobi', sa.Float(), **MONEY))
    op.add_column('payrolls', sa.Column('loan_repayment', sa.Float(), **MONEY))
    op.add_column('payrolls', sa.Column('loan_repayment_note', sa.String(), nullable=True))
    op.add_column('payrolls', sa.Column('advance_recovery', sa.Float(), **MONEY))
    op.add_column('payrolls', sa.Column('advance_recovery_note', sa.String(), nullable=True))
    op.add_column('payrolls', sa.Column('other_deduction', sa.Float(), **MONEY))
    op.add_column('payrolls', sa.Column('other_deduction_note', sa.String(), nullable=True))

    # --- payment details ---
    op.add_column('payrolls', sa.Column('payment_method', sa.String(), nullable=True))
    op.add_column('payrolls', sa.Column('payment_reference', sa.String(), nullable=True))

    # --- payslip identity & revisions ---
    op.add_column('payrolls', sa.Column('payslip_no', sa.String(), nullable=True))
    op.add_column('payrolls', sa.Column('verification_code', sa.String(), nullable=True))
    op.add_column('payrolls', sa.Column('version', sa.Integer(), nullable=False, server_default='1'))
    op.add_column('payrolls', sa.Column('generated_at', sa.DateTime(), nullable=True))
    op.add_column('payrolls', sa.Column('supersedes_id', sa.Integer(), nullable=True))
    op.add_column('payrolls', sa.Column('correction_reason', sa.String(), nullable=True))
    op.add_column('payrolls', sa.Column('cancelled_at', sa.DateTime(), nullable=True))
    op.add_column('payrolls', sa.Column('cancellation_reason', sa.String(), nullable=True))

    op.create_unique_constraint('uq_payrolls_payslip_no', 'payrolls', ['payslip_no'])
    op.create_unique_constraint('uq_payrolls_verification_code', 'payrolls', ['verification_code'])
    op.create_index('ix_payrolls_payslip_no', 'payrolls', ['payslip_no'])
    op.create_index('ix_payrolls_verification_code', 'payrolls', ['verification_code'])
    op.create_foreign_key(
        'fk_payrolls_supersedes_id', 'payrolls', 'payrolls', ['supersedes_id'], ['id'],
    )

    # Existing payslips carry only the totals. Park each total on its "other"
    # line so the itemised view still adds up to the same number.
    op.execute('UPDATE payrolls SET other_allowance = COALESCE(allowances, 0) WHERE COALESCE(allowances, 0) <> 0')
    op.execute('UPDATE payrolls SET other_deduction = COALESCE(deductions, 0) WHERE COALESCE(deductions, 0) <> 0')

    # New status members. autocommit_block because PostgreSQL refuses
    # ALTER TYPE ... ADD VALUE inside a transaction on older servers.
    with op.get_context().autocommit_block():
        for value in NEW_STATUSES:
            op.execute(f"ALTER TYPE payrollstatusenum ADD VALUE IF NOT EXISTS '{value}'")

    # --- disputes ---
    op.create_table(
        'payslip_disputes',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('payroll_id', sa.Integer(), nullable=False),
        sa.Column('employee_id', sa.Integer(), nullable=False),
        sa.Column('category', sa.Enum(
            'INCORRECT_SALARY', 'MISSING_ALLOWANCE', 'UNEXPECTED_DEDUCTION',
            'TAX_ISSUE', 'BANK_PAYMENT_ISSUE', 'OTHER',
            name='disputecategoryenum',
        ), nullable=False),
        sa.Column('comment', sa.Text(), nullable=False),
        sa.Column('attachment_url', sa.String(), nullable=True),
        sa.Column('status', sa.Enum(
            'OPEN', 'IN_REVIEW', 'RESOLVED', 'REJECTED', name='disputestatusenum',
        ), nullable=False, server_default='OPEN'),
        sa.Column('response', sa.Text(), nullable=True),
        sa.Column('responded_by', sa.Integer(), nullable=True),
        sa.Column('responded_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['payroll_id'], ['payrolls.id'], ),
        sa.ForeignKeyConstraint(['employee_id'], ['users.id'], ),
        sa.ForeignKeyConstraint(['responded_by'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_payslip_disputes_id', 'payslip_disputes', ['id'])
    op.create_index('ix_payslip_disputes_payroll_id', 'payslip_disputes', ['payroll_id'])
    op.create_index('ix_payslip_disputes_employee_id', 'payslip_disputes', ['employee_id'])


def downgrade() -> None:
    op.drop_index('ix_payslip_disputes_employee_id', table_name='payslip_disputes')
    op.drop_index('ix_payslip_disputes_payroll_id', table_name='payslip_disputes')
    op.drop_index('ix_payslip_disputes_id', table_name='payslip_disputes')
    op.drop_table('payslip_disputes')
    sa.Enum(name='disputecategoryenum').drop(op.get_bind(), checkfirst=True)
    sa.Enum(name='disputestatusenum').drop(op.get_bind(), checkfirst=True)

    op.drop_constraint('fk_payrolls_supersedes_id', 'payrolls', type_='foreignkey')
    op.drop_index('ix_payrolls_verification_code', table_name='payrolls')
    op.drop_index('ix_payrolls_payslip_no', table_name='payrolls')
    op.drop_constraint('uq_payrolls_verification_code', 'payrolls', type_='unique')
    op.drop_constraint('uq_payrolls_payslip_no', 'payrolls', type_='unique')

    for column in (
        'cancellation_reason', 'cancelled_at', 'correction_reason', 'supersedes_id',
        'generated_at', 'version', 'verification_code', 'payslip_no',
        'payment_reference', 'payment_method',
        'other_deduction_note', 'other_deduction', 'advance_recovery_note',
        'advance_recovery', 'loan_repayment_note', 'loan_repayment', 'eobi',
        'social_security', 'provident_fund',
        'other_allowance_note', 'other_allowance', 'medical_allowance',
        'transport_allowance', 'house_allowance',
    ):
        op.drop_column('payrolls', column)

    # PostgreSQL cannot drop a value from an enum type; removing PROCESSING /
    # CORRECTED / CANCELLED would mean recreating payrollstatusenum and
    # rewriting every row. They are left in place — harmless once unused.
