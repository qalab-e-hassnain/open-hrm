"""leave year rollover payroll uniqueness holidays

Revision ID: 314c5417c22f
Revises: 6fe3c6ec16f5
Create Date: 2026-09-04 03:10:45.500665

"""
import datetime

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '314c5417c22f'
down_revision = '6fe3c6ec16f5'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- leave balances become per year -------------------------------------
    # Uniqueness was on employee_id alone, which made the year column a lie:
    # opening 2027 collided with the employee's 2026 row, so the first leave
    # request of the year failed outright.
    op.drop_constraint("leave_balances_employee_id_key", "leave_balances", type_="unique")
    op.create_index("ix_leave_balances_employee_id", "leave_balances", ["employee_id"])
    op.create_unique_constraint("uq_leave_balance_employee_year", "leave_balances",
                                ["employee_id", "year"])

    # --- one payslip per employee per period --------------------------------
    # Enforced by the database rather than by whichever code path creates it.
    op.create_unique_constraint("uq_payroll_employee_period", "payrolls",
                                ["employee_id", "month", "year"])

    # --- attendance indexes -------------------------------------------------
    # Every attendance query filters on employee and a date range; the table
    # had nothing but its primary key and grows fastest of all.
    op.create_index("ix_attendances_employee_id", "attendances", ["employee_id"])
    op.create_index("ix_attendances_employee_checkin", "attendances",
                    ["employee_id", "check_in_time"])

    # --- public holidays ----------------------------------------------------
    op.create_table(
        "holidays",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("holiday_date", sa.Date(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("is_confirmed", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("is_recurring", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("holiday_date", name="uq_holiday_date"),
    )
    op.create_index("ix_holidays_holiday_date", "holidays", ["holiday_date"])
    op.create_index("ix_holidays_id", "holidays", ["id"])

    # Pakistan's fixed-date national holidays, seeded for 2025-2027. Only these
    # are seeded: Eid-ul-Fitr, Eid-ul-Adha, Ashura and Eid Milad-un-Nabi follow
    # the Islamic calendar and are set by official announcement, often weeks
    # ahead. Guessing them here would put wrong dates into leave calculations
    # with the authority of a migration, so they are left for an admin to enter
    # once announced.
    fixed = [
        (2, 5,  "Kashmir Solidarity Day"),
        (3, 23, "Pakistan Day"),
        (5, 1,  "Labour Day"),
        (8, 14, "Independence Day"),
        (11, 9, "Iqbal Day"),
        (12, 25, "Quaid-e-Azam Day / Christmas"),
    ]
    rows = []
    for year in (2025, 2026, 2027):
        for month, day, name in fixed:
            rows.append({
                "holiday_date": datetime.date(year, month, day),
                "name": f"{name}",
                "is_confirmed": True,
                "is_recurring": True,
            })
    op.bulk_insert(sa.table(
        "holidays",
        sa.column("holiday_date", sa.Date),
        sa.column("name", sa.String),
        sa.column("is_confirmed", sa.Boolean),
        sa.column("is_recurring", sa.Boolean),
    ), rows)


def downgrade() -> None:
    op.drop_table("holidays")
    op.drop_index("ix_attendances_employee_checkin", table_name="attendances")
    op.drop_index("ix_attendances_employee_id", table_name="attendances")
    op.drop_constraint("uq_payroll_employee_period", "payrolls", type_="unique")
    op.drop_constraint("uq_leave_balance_employee_year", "leave_balances", type_="unique")
    op.drop_index("ix_leave_balances_employee_id", table_name="leave_balances")
    op.create_unique_constraint("leave_balances_employee_id_key", "leave_balances", ["employee_id"])
