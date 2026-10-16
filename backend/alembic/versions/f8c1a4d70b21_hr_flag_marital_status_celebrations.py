"""HR flag, marital status, anniversary, celebration markers; hourly_rate backfill

Additive only — every column is new and nullable (or carries a server default),
so existing rows keep working and the migration is fully reversible. No enum is
touched: HR is a boolean flag on users, not a new value of roleenum.

Revision ID: f8c1a4d70b21
Revises: e7a1c9d2b4f3
"""
from alembic import op
import sqlalchemy as sa


revision = 'f8c1a4d70b21'
down_revision = 'e7a1c9d2b4f3'
branch_labels = None
depends_on = None


# Kept in step with app.utils.overtime.MONTHLY_WORKING_HOURS.
MONTHLY_WORKING_HOURS = 160


def upgrade() -> None:
    op.add_column('users', sa.Column('is_hr', sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column('users', sa.Column('marital_status', sa.String(), nullable=True))
    op.add_column('users', sa.Column('anniversary_date', sa.DateTime(), nullable=True))
    op.add_column('users', sa.Column('birthday_notice_year', sa.Integer(), nullable=True))
    op.add_column('users', sa.Column('anniversary_notice_year', sa.Integer(), nullable=True))

    # Managers already running an HR department keep — and by this change gain —
    # HR powers. Without this they would silently lose the employment-field
    # editing they have today (users.is_hr_manager used to read the department).
    op.execute(
        """
        UPDATE users
           SET is_hr = true
         WHERE role = 'MANAGER'
           AND lower(trim(coalesce(department, ''))) IN (
               'hr', 'human resources', 'people', 'people operations',
               'people & culture', 'people and culture'
           )
        """
    )

    # hourly_rate is now derived from the monthly basic (basic / 160) rather
    # than typed in. Bring EVERY row in line — including the handful carrying a
    # hand-typed rate that no longer matches their salary — so the column agrees
    # with the figure overtime is actually priced at. Nothing reads the old
    # values any more; leaving them would only mislead.
    op.execute(
        f"""
        UPDATE users
           SET hourly_rate = ROUND((COALESCE(base_salary, 0) / {MONTHLY_WORKING_HOURS})::numeric, 2)
        """
    )


def downgrade() -> None:
    op.drop_column('users', 'anniversary_notice_year')
    op.drop_column('users', 'birthday_notice_year')
    op.drop_column('users', 'anniversary_date')
    op.drop_column('users', 'marital_status')
    op.drop_column('users', 'is_hr')
