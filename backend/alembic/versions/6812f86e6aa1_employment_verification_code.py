"""employment verification code

Revision ID: 6812f86e6aa1
Revises: df4414ee3f4d
Create Date: 2026-09-03 21:06:18.043150

"""
import secrets

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '6812f86e6aa1'
down_revision = 'df4414ee3f4d'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """
    Per-employee code printed on an employment letter, so a bank can confirm
    the letter through a public endpoint instead of a login wall.

    Existing users are backfilled here rather than lazily: a letter issued the
    moment this ships must already have a working code, and generating one on
    first read would mean an unauthenticated GET could write to the database.
    """
    op.add_column("users", sa.Column("employment_verification_code", sa.String(), nullable=True))
    op.create_index("ix_users_employment_verification_code", "users",
                    ["employment_verification_code"], unique=True)

    conn = op.get_bind()
    ids = [r[0] for r in conn.execute(sa.text(
        "SELECT id FROM users WHERE employment_verification_code IS NULL"))]
    for uid in ids:
        conn.execute(
            sa.text("UPDATE users SET employment_verification_code = :c WHERE id = :i"),
            {"c": secrets.token_urlsafe(12), "i": uid},
        )


def downgrade() -> None:
    op.drop_index("ix_users_employment_verification_code", table_name="users")
    op.drop_column("users", "employment_verification_code")

