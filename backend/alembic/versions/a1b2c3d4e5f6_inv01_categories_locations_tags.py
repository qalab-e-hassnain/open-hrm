"""inv01: categories, locations and the tag counter, with the seed data

Phase 1 of the lab inventory module. Creates the three tables the item master
will point at, and seeds them:

  * the ten legacy asset categories, named so that lowercasing and
    underscoring the name reproduces the old `assetcategoryenum` value exactly
    ("Access card" -> "access_card"). That is what lets the deprecated
    /api/v1/assets endpoints keep returning the strings the portal already
    parses, without a second lookup table to maintain.
  * the six lab categories from section 5 of the design.
  * the location skeleton from section 4. Codes here are the codes printed on
    the shelves — a location that is not physically labelled does not exist.

Postgres. The test suite builds its schema with Base.metadata.create_all and
never runs these.

Revision ID: a1b2c3d4e5f6
Revises: 1133c5c04e24
Create Date: 2026-09-20 09:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'a1b2c3d4e5f6'
down_revision = '1133c5c04e24'
branch_labels = None
depends_on = None


# (name, tracking_type, tag_prefix, serial_required, audit_days)
LEGACY_CATEGORIES = [
    ("Laptop", "serial", "QGT-EQ-", True, 30),
    ("Desktop", "serial", "QGT-EQ-", True, 30),
    ("Monitor", "serial", "QGT-EQ-", False, 30),
    ("Phone", "serial", "QGT-EQ-", True, 30),
    ("SIM", "serial", None, False, 30),
    ("Access card", "serial", None, False, 30),
    ("Peripheral", "serial", "QGT-EQ-", False, 30),
    ("Furniture", "serial", None, False, 180),
    ("Licence", "serial", None, False, 90),
    ("Other", "serial", None, False, 30),
]

LAB_CATEGORIES = [
    ("Test and measurement", "serial", "QGT-EQ-", True, 30),
    ("Dev kits and compute", "serial", "QGT-DK-", True, 30),
    ("Sensors and modules", "serial", "QGT-MD-", False, 30),
    ("Prototype boards", "project", "QGT-PR-", False, 90),
    ("Client hardware", "project", "QGT-CL-", False, 90),
    ("Components and consumables", "quantity", "QGT-ST-", False, 180),
]

# (code, name, parent_code, location_type)
LOCATIONS = [
    ("SITE", "NSTP NUST, Islamabad", None, "internal"),
    ("L1", "Lab 1 — Office 3405", "SITE", "internal"),
    ("L2", "Lab 2 — Office 3406", "SITE", "internal"),
    ("STORE", "Store room", "SITE", "internal"),
    ("QUAR", "Quarantine", "SITE", "quarantine"),
    ("EXT", "External", None, "internal"),
    ("EXT-TRANSIT", "In transit", "EXT", "in_transit"),
    ("EXT-CUSTOMS", "Customs hold", "EXT", "customs_hold"),
]


def upgrade() -> None:
    op.create_table(
        'inv_categories',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('tracking_type', sa.String(), nullable=False, server_default='serial'),
        sa.Column('tag_prefix', sa.String(), nullable=True),
        sa.Column('serial_required', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('default_audit_days', sa.Integer(), nullable=False, server_default='30'),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('created_at', sa.DateTime(), nullable=False,
                  server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('name', name='uq_inv_categories_name'),
    )
    op.create_index('ix_inv_categories_id', 'inv_categories', ['id'])
    op.create_index('ix_inv_categories_name', 'inv_categories', ['name'])

    op.create_table(
        'inv_locations',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('parent_id', sa.Integer(), nullable=True),
        sa.Column('code', sa.String(), nullable=False),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('location_type', sa.String(), nullable=False, server_default='internal'),
        sa.Column('client_id', sa.Integer(), nullable=True),
        sa.Column('client_name', sa.String(), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False,
                  server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.ForeignKeyConstraint(['parent_id'], ['inv_locations.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('code', name='uq_inv_locations_code'),
    )
    op.create_index('ix_inv_locations_id', 'inv_locations', ['id'])
    op.create_index('ix_inv_locations_code', 'inv_locations', ['code'])
    op.create_index('ix_inv_locations_parent_id', 'inv_locations', ['parent_id'])
    op.create_index('ix_inv_locations_type', 'inv_locations', ['location_type'])
    op.create_index('ix_inv_locations_client_id', 'inv_locations', ['client_id'])

    op.create_table(
        'inv_tag_counters',
        sa.Column('prefix', sa.String(), nullable=False),
        sa.Column('next_number', sa.Integer(), nullable=False, server_default='1'),
        sa.PrimaryKeyConstraint('prefix'),
    )

    categories = sa.table(
        'inv_categories',
        sa.column('name', sa.String), sa.column('tracking_type', sa.String),
        sa.column('tag_prefix', sa.String), sa.column('serial_required', sa.Boolean),
        sa.column('default_audit_days', sa.Integer))
    op.bulk_insert(categories, [
        {"name": n, "tracking_type": t, "tag_prefix": p,
         "serial_required": s, "default_audit_days": d}
        for n, t, p, s, d in LEGACY_CATEGORIES + LAB_CATEGORIES])

    locations = sa.table(
        'inv_locations',
        sa.column('code', sa.String), sa.column('name', sa.String),
        sa.column('location_type', sa.String))
    op.bulk_insert(locations, [
        {"code": c, "name": n, "location_type": t} for c, n, _, t in LOCATIONS])

    # Parents in a second pass, so the insert order above does not have to be
    # a valid topological sort of a tree somebody will later reshape.
    for code, _, parent_code, _ in LOCATIONS:
        if parent_code:
            op.execute(
                f"UPDATE inv_locations SET parent_id = "
                f"(SELECT id FROM inv_locations WHERE code = '{parent_code}') "
                f"WHERE code = '{code}'")

    counters = sa.table('inv_tag_counters', sa.column('prefix', sa.String),
                        sa.column('next_number', sa.Integer))
    op.bulk_insert(counters, [
        {"prefix": p, "next_number": 1}
        for p in ("QGT-EQ-", "QGT-DK-", "QGT-MD-", "QGT-PR-", "QGT-CL-", "QGT-ST-")])


def downgrade() -> None:
    op.drop_table('inv_tag_counters')
    op.drop_index('ix_inv_locations_client_id', table_name='inv_locations')
    op.drop_index('ix_inv_locations_type', table_name='inv_locations')
    op.drop_index('ix_inv_locations_parent_id', table_name='inv_locations')
    op.drop_index('ix_inv_locations_code', table_name='inv_locations')
    op.drop_index('ix_inv_locations_id', table_name='inv_locations')
    op.drop_table('inv_locations')
    op.drop_index('ix_inv_categories_name', table_name='inv_categories')
    op.drop_index('ix_inv_categories_id', table_name='inv_categories')
    op.drop_table('inv_categories')
