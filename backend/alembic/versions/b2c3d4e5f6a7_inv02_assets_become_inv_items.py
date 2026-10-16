"""inv02: assets becomes inv_items

The load-bearing migration. `assets` is renamed rather than replaced, so every
id already quoted in a clearance line, a settlement or an audit row still
points at the same physical object — and, more to the point, so there is one
custody register rather than two. A second register would be invisible to the
offboarding gate, which is the feature the whole module exists for.

Three conversions happen here:

  * `category` (native enum) becomes `category_id`, a foreign key into the
    categories seeded by inv01.
  * `status` and `condition` stop being native Postgres enums and become
    String columns validated in Python. Adding a value to a native enum means
    ALTER TYPE, which cannot be used in the same migration that writes rows
    carrying the new value — and this module adds four statuses. Note the
    values stored were the enum NAMES ('IN_STOCK'), so the conversion
    lowercases.
  * the free-text `location` column added in 1133c5c04e24 is matched against
    the seeded location codes where it can be, and preserved in `notes` where
    it cannot. Nothing typed into it is thrown away.

Postgres only. The test suite builds its schema with create_all.

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-09-20 09:10:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'b2c3d4e5f6a7'
down_revision = 'a1b2c3d4e5f6'
branch_labels = None
depends_on = None


CATEGORY_MAP = {
    "LAPTOP": "Laptop", "DESKTOP": "Desktop", "MONITOR": "Monitor",
    "PHONE": "Phone", "SIM": "SIM", "ACCESS_CARD": "Access card",
    "PERIPHERAL": "Peripheral", "FURNITURE": "Furniture",
    "LICENCE": "Licence", "OTHER": "Other",
}

STATUS_MAP = {
    "IN_STOCK": "in_stock", "ISSUED": "issued", "IN_REPAIR": "under_repair",
    "RETIRED": "scrapped", "LOST": "lost",
}

# GOOD and FAIR both land on `working`. The distinction had no behaviour
# attached to it anywhere in the codebase, and the design's vocabulary is
# new/working/faulty/scrapped; the original word is kept in `notes` below so
# nothing is silently lost.
CONDITION_MAP = {
    "NEW": "new", "GOOD": "working", "FAIR": "working", "DAMAGED": "faulty",
}


def _case(column: str, mapping: dict, default: str) -> str:
    whens = " ".join(f"WHEN '{k}' THEN '{v}'" for k, v in mapping.items())
    return f"CASE {column}::text {whens} ELSE '{default}' END"


def upgrade() -> None:
    op.rename_table('assets', 'inv_items')

    op.add_column('inv_items', sa.Column('category_id', sa.Integer(), nullable=True))
    op.add_column('inv_items', sa.Column('manufacturer', sa.String(), nullable=True))
    op.add_column('inv_items', sa.Column('mpn', sa.String(), nullable=True))
    op.add_column('inv_items', sa.Column('currency', sa.String(), nullable=True))
    op.add_column('inv_items', sa.Column('supplier', sa.String(), nullable=True))
    op.add_column('inv_items', sa.Column('invoice_ref', sa.String(), nullable=True))
    op.add_column('inv_items', sa.Column('unit_cost', sa.Float(), nullable=True))
    op.add_column('inv_items', sa.Column('ownership', sa.String(), nullable=False,
                                         server_default='company'))
    op.add_column('inv_items', sa.Column('lifecycle_state', sa.String(), nullable=True))
    op.add_column('inv_items', sa.Column('client_id', sa.Integer(), nullable=True))
    op.add_column('inv_items', sa.Column('client_name', sa.String(), nullable=True))
    op.add_column('inv_items', sa.Column('project_id', sa.Integer(), nullable=True))
    op.add_column('inv_items', sa.Column('project_code', sa.String(), nullable=True))
    op.add_column('inv_items', sa.Column('project_name', sa.String(), nullable=True))
    op.add_column('inv_items', sa.Column('current_location_id', sa.Integer(), nullable=True))
    op.add_column('inv_items', sa.Column('current_holder_id', sa.Integer(), nullable=True))
    op.add_column('inv_items', sa.Column('hold_due_date', sa.Date(), nullable=True))
    op.add_column('inv_items', sa.Column('held_since', sa.DateTime(), nullable=True))
    op.add_column('inv_items', sa.Column('quarantined_at', sa.DateTime(), nullable=True))
    op.add_column('inv_items', sa.Column('qty', sa.Float(), nullable=False,
                                         server_default='1'))
    op.add_column('inv_items', sa.Column('min_qty', sa.Float(), nullable=True))
    op.add_column('inv_items', sa.Column('photo_url', sa.String(), nullable=True))
    op.add_column('inv_items', sa.Column('datasheet_url', sa.String(), nullable=True))

    # --- category enum -> foreign key -------------------------------------
    op.execute(f"""
        UPDATE inv_items i SET category_id = c.id
          FROM inv_categories c
         WHERE c.name = {_case('i.category', CATEGORY_MAP, 'Other')}
    """)
    op.execute("""
        UPDATE inv_items SET category_id =
          (SELECT id FROM inv_categories WHERE name = 'Other')
         WHERE category_id IS NULL
    """)
    op.alter_column('inv_items', 'category_id', nullable=False)
    op.create_foreign_key('fk_inv_items_category', 'inv_items', 'inv_categories',
                          ['category_id'], ['id'])
    op.create_index('ix_inv_items_category_id', 'inv_items', ['category_id'])
    op.drop_index('ix_assets_category', table_name='inv_items')
    op.drop_column('inv_items', 'category')
    op.execute("DROP TYPE IF EXISTS assetcategoryenum")

    # --- status and condition: native enum -> String -----------------------
    op.add_column('inv_items', sa.Column('status_new', sa.String(), nullable=True))
    op.add_column('inv_items', sa.Column('condition_new', sa.String(), nullable=True))
    op.execute(f"UPDATE inv_items SET status_new = {_case('status', STATUS_MAP, 'in_stock')}")
    op.execute(f"UPDATE inv_items SET condition_new = "
               f"{_case('condition', CONDITION_MAP, 'working')}")
    # Keep the word that was there, where it no longer has a home of its own.
    op.execute("""
        UPDATE inv_items
           SET notes = COALESCE(notes || E'\\n', '') ||
                       'Condition recorded before the inventory migration: ' ||
                       lower(condition::text)
         WHERE condition::text IN ('FAIR', 'GOOD')
    """)
    op.drop_index('ix_assets_status', table_name='inv_items')
    op.drop_column('inv_items', 'status')
    op.drop_column('inv_items', 'condition')
    op.alter_column('inv_items', 'status_new', new_column_name='status',
                    nullable=False, server_default='in_stock')
    op.alter_column('inv_items', 'condition_new', new_column_name='condition',
                    nullable=False, server_default='working')
    op.create_index('ix_inv_items_status', 'inv_items', ['status'])
    op.execute("DROP TYPE IF EXISTS assetstatusenum")

    # --- custody, derived from the open assignments ------------------------
    op.execute("""
        UPDATE inv_items i
           SET current_holder_id = a.employee_id,
               held_since = a.issued_at,
               hold_due_date = COALESCE(a.expected_return_date,
                                        (a.issued_at + INTERVAL '30 days')::date)
          FROM asset_assignments a
         WHERE a.asset_id = i.id AND a.returned_at IS NULL
    """)

    # --- the free-text location column -------------------------------------
    op.execute("""
        UPDATE inv_items i SET current_location_id = l.id
          FROM inv_locations l
         WHERE i.current_holder_id IS NULL
           AND i.location IS NOT NULL
           AND upper(trim(i.location)) = l.code
    """)
    # Only where the text cannot be recovered from the code. An item out with
    # somebody has no shelf by definition, so without the NOT EXISTS every
    # held item would pick up a note repeating a code the tree already knows.
    op.execute("""
        UPDATE inv_items
           SET notes = COALESCE(notes || E'\\n', '') ||
                       'Location before the inventory migration: ' || location
         WHERE location IS NOT NULL
           AND current_location_id IS NULL
           AND NOT EXISTS (SELECT 1 FROM inv_locations l
                            WHERE l.code = upper(trim(inv_items.location)))
    """)
    # Everything still unplaced and not with a person goes to the store room,
    # which is where the invariant says it has to be: exactly one of a shelf or
    # a holder, never neither.
    op.execute("""
        UPDATE inv_items
           SET current_location_id = (SELECT id FROM inv_locations WHERE code = 'STORE')
         WHERE current_holder_id IS NULL
           AND current_location_id IS NULL
           AND status NOT IN ('scrapped', 'lost', 'in_transit')
    """)
    op.drop_index('ix_assets_location', table_name='inv_items')
    op.drop_column('inv_items', 'location')

    op.create_foreign_key('fk_inv_items_location', 'inv_items', 'inv_locations',
                          ['current_location_id'], ['id'])
    op.create_foreign_key('fk_inv_items_holder', 'inv_items', 'users',
                          ['current_holder_id'], ['id'], ondelete='RESTRICT')
    op.create_index('ix_inv_items_location', 'inv_items', ['current_location_id'])
    op.create_index('ix_inv_items_holder', 'inv_items', ['current_holder_id'])
    op.create_index('ix_inv_items_holder_due', 'inv_items',
                    ['current_holder_id', 'hold_due_date'])
    op.create_index('ix_inv_items_mpn', 'inv_items', ['mpn'])
    op.create_index('ix_inv_items_ownership', 'inv_items', ['ownership'])
    op.create_index('ix_inv_items_project_id', 'inv_items', ['project_id'])
    op.create_index('ix_inv_items_client_id', 'inv_items', ['client_id'])

    # Indexes carried over from the old table keep their old names after a
    # rename. Renaming them costs nothing and stops the next person wondering
    # why inv_items is indexed by something called ix_assets_tag.
    for old, new in (('ix_assets_tag', 'ix_inv_items_tag'),
                     ('ix_assets_id', 'ix_inv_items_id'),
                     ('ix_assets_serial_number', 'ix_inv_items_serial_number')):
        op.execute(f"ALTER INDEX IF EXISTS {old} RENAME TO {new}")

    # Exactly one of holder or location, for anything still in circulation.
    # Scrapped, lost and in-transit items legitimately have neither, so the
    # rule is conditioned rather than absolute.
    op.create_check_constraint(
        'ck_inv_items_placed', 'inv_items',
        "status IN ('scrapped', 'lost', 'in_transit') "
        "OR num_nonnulls(current_location_id, current_holder_id) = 1")


def downgrade() -> None:
    raise NotImplementedError(
        "inv02 is not reversible: the category enum and the free-text location "
        "column are folded into rows that cannot be told apart afterwards. "
        "Restore from a backup taken before the upgrade.")
