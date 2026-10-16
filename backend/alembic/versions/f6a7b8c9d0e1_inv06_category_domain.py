"""inv06: lab and office are separate sections of one register

The asset register came across whole in inv02, so laptops, phones and SIMs
showed up on the lab screens, and every laptop issued before the move was given
a 30-day hold it had long outlived — the digest called them all overdue.

Office equipment is issued for as long as the job lasts. Its categories are
marked `office`, and its holds are cleared.

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-10-01 10:30:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'f6a7b8c9d0e1'
down_revision = 'e5f6a7b8c9d0'
branch_labels = None
depends_on = None

# The categories inv01 created for the old register's enum values.
OFFICE_CATEGORIES = ("Laptop", "Desktop", "Monitor", "Phone", "SIM", "Access card",
                     "Peripheral", "Furniture", "Licence", "Other")


def upgrade() -> None:
    op.add_column('inv_categories', sa.Column('domain', sa.String(), nullable=False,
                                              server_default='lab'))
    op.create_index('ix_inv_categories_domain', 'inv_categories', ['domain'])
    names = ", ".join(f"'{n}'" for n in OFFICE_CATEGORIES)
    op.execute(f"UPDATE inv_categories SET domain = 'office' WHERE name IN ({names})")
    op.execute("""
        UPDATE inv_items SET hold_due_date = NULL
         WHERE category_id IN (SELECT id FROM inv_categories WHERE domain = 'office')
    """)


def downgrade() -> None:
    # The cleared hold dates are not restored: they were wrong.
    op.drop_index('ix_inv_categories_domain', table_name='inv_categories')
    op.drop_column('inv_categories', 'domain')
