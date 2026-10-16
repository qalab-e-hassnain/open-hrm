"""Record which signal placed a check-in

A check-in can now be placed by the office network as well as by a GPS fix,
and the two are not equally strong evidence: coordinates are typed into the
request by the client, a source address is assigned by the network. Without
this column an audit cannot tell them apart after the fact.

Null on every row written before today.

Revision ID: a7b8c9d0e1f2
Revises: f6a7b8c9d0e1
"""
import sqlalchemy as sa
from alembic import op

revision = "a7b8c9d0e1f2"
down_revision = "f6a7b8c9d0e1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("attendances", sa.Column("verified_by", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("attendances", "verified_by")
