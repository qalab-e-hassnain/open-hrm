"""add manual unapproved leave fine to payrolls

Revision ID: a9d4e6f2c8b1
Revises: f8c1a4d70b21
Create Date: 2026-07-31
"""
from alembic import op
import sqlalchemy as sa


revision = "a9d4e6f2c8b1"
down_revision = "f8c1a4d70b21"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "payrolls",
        sa.Column(
            "unapproved_leave_fine",
            sa.Float(),
            nullable=False,
            server_default="0",
        ),
    )


def downgrade() -> None:
    op.drop_column("payrolls", "unapproved_leave_fine")
