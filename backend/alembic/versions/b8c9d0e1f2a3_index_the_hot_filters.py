"""Index the columns the hot queries actually filter on

`attendances` is the most-read table in the system — every dashboard load,
every report, the auto-checkout sweep — and carried no index but its primary
key, so each of those was a sequential scan. Same for `leaves`, which every
balance and payroll calculation filters by employee and status.

The indexes match the queries as written: employee plus the column the range
is taken on, and a partial index for the open-session sweep, which asks only
for rows where check_out_time IS NULL and is run on most attendance requests.

At today's row counts Postgres may still choose a scan — 328 rows fit in a
page or two. These are for the size these tables reach, not the size they are:
attendance grows by about twenty rows a working day.

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
"""
from alembic import op

revision = "b8c9d0e1f2a3"
down_revision = "a7b8c9d0e1f2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("ix_attendance_employee_checkin", "attendances",
                    ["employee_id", "check_in_time"])
    # The auto-checkout sweep asks only for open sessions, which are a handful
    # of rows out of the whole table.
    op.execute("CREATE INDEX IF NOT EXISTS ix_attendance_open "
               "ON attendances (employee_id) WHERE check_out_time IS NULL")
    op.create_index("ix_leave_employee_status", "leaves",
                    ["employee_id", "status"])
    op.create_index("ix_leave_period", "leaves", ["start_date", "end_date"])
    op.create_index("ix_reimbursement_employee_status", "reimbursements",
                    ["employee_id", "status"])


def downgrade() -> None:
    op.drop_index("ix_reimbursement_employee_status", table_name="reimbursements")
    op.drop_index("ix_leave_period", table_name="leaves")
    op.drop_index("ix_leave_employee_status", table_name="leaves")
    op.execute("DROP INDEX IF EXISTS ix_attendance_open")
    op.drop_index("ix_attendance_employee_checkin", table_name="attendances")
