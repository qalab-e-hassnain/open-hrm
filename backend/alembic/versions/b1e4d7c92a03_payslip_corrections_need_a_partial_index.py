"""payslip corrections need a partial unique index

A plain unique constraint on (employee_id, month, year) made it impossible to
store a corrected payslip beside the original, so /payslips/{id}/correct failed
with an integrity error on every attempt — the feature could never have worked.

A correction is meant to keep the original and add a new version next to it, so
the uniqueness that matters is one *live* payslip per employee per month.
Superseded and cancelled rows are excluded.

Revision ID: b1e4d7c92a03
Revises: 9ac6c73f3a06
"""
from alembic import op
import sqlalchemy as sa

revision = 'b1e4d7c92a03'
down_revision = '9ac6c73f3a06'
branch_labels = None
depends_on = None

_WHERE = "status NOT IN ('CORRECTED', 'CANCELLED')"


def upgrade() -> None:
    op.drop_constraint('uq_payroll_employee_period', 'payrolls', type_='unique')
    op.create_index('uq_payroll_active_period', 'payrolls',
                    ['employee_id', 'month', 'year'], unique=True,
                    postgresql_where=sa.text(_WHERE))


def downgrade() -> None:
    # Only reversible while no period holds more than one live payslip, which
    # is exactly what the partial index guarantees.
    op.drop_index('uq_payroll_active_period', table_name='payrolls')
    op.create_unique_constraint('uq_payroll_employee_period', 'payrolls',
                                ['employee_id', 'month', 'year'])
