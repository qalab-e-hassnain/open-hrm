"""inv03: the append-only movement log, backfilled from asset_assignments

`asset_assignments` stored a hold as one row that got UPDATEd on return. That
shape can express an issue and a return and nothing else — not a transfer, not
a shipment, not a consume, not a correction. This replaces it with a log.

Each old assignment becomes one `checkout` movement, plus a `checkin` where it
was returned. `condition_out` and `condition_in` become the `condition` of
their respective rows, which is the correct shape: the pair of columns only
existed to fit two events onto one row.

The old table is deliberately NOT dropped. It stays as a read-only rollback
path for one release; a follow-up migration removes it once the new log has
been trusted in production for a while.

The append-only trigger is the real guarantee — it holds against psql and
against code nobody has written yet. There is a matching session guard in
app/models/inventory.py, because the test suite builds its schema with
create_all and would otherwise never exercise the invariant at all.

Postgres only.

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-09-20 09:20:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'c3d4e5f6a7b8'
down_revision = 'b2c3d4e5f6a7'
branch_labels = None
depends_on = None


CONDITION_CASE = ("CASE {col}::text WHEN 'NEW' THEN 'new' WHEN 'GOOD' THEN 'working' "
                  "WHEN 'FAIR' THEN 'working' WHEN 'DAMAGED' THEN 'faulty' "
                  "ELSE 'working' END")


def upgrade() -> None:
    op.create_table(
        'inv_movements',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('item_id', sa.Integer(), nullable=False),
        sa.Column('movement_type', sa.String(), nullable=False),
        sa.Column('from_location_id', sa.Integer(), nullable=True),
        sa.Column('to_location_id', sa.Integer(), nullable=True),
        sa.Column('from_holder_id', sa.Integer(), nullable=True),
        sa.Column('to_holder_id', sa.Integer(), nullable=True),
        sa.Column('qty_delta', sa.Float(), nullable=False, server_default='0'),
        sa.Column('project_id', sa.Integer(), nullable=True),
        sa.Column('unit_cost', sa.Float(), nullable=True),
        sa.Column('performed_by', sa.Integer(), nullable=True),
        sa.Column('reason', sa.Text(), nullable=True),
        sa.Column('reference', sa.String(), nullable=True),
        sa.Column('expected_return_date', sa.Date(), nullable=True),
        sa.Column('condition', sa.String(), nullable=True),
        sa.Column('recovery_amount', sa.Float(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False,
                  server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.ForeignKeyConstraint(['item_id'], ['inv_items.id']),
        sa.ForeignKeyConstraint(['from_location_id'], ['inv_locations.id']),
        sa.ForeignKeyConstraint(['to_location_id'], ['inv_locations.id']),
        sa.ForeignKeyConstraint(['from_holder_id'], ['users.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['to_holder_id'], ['users.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['performed_by'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_inv_movements_id', 'inv_movements', ['id'])
    op.create_index('ix_inv_movements_item_id', 'inv_movements', ['item_id'])
    op.create_index('ix_inv_movements_type', 'inv_movements', ['movement_type'])
    op.create_index('ix_inv_movements_created_at', 'inv_movements', ['created_at'])
    op.create_index('ix_inv_movements_from_holder', 'inv_movements', ['from_holder_id'])
    op.create_index('ix_inv_movements_to_holder', 'inv_movements', ['to_holder_id'])
    op.create_index('ix_inv_movements_project_id', 'inv_movements', ['project_id'])
    op.create_index('ix_inv_movements_item_time', 'inv_movements',
                    ['item_id', 'created_at'])
    op.create_index('ix_inv_movements_project_type', 'inv_movements',
                    ['project_id', 'movement_type'])

    # --- backfill: one checkout per assignment -----------------------------
    op.execute(f"""
        INSERT INTO inv_movements
            (item_id, movement_type, to_holder_id, performed_by, reason,
             expected_return_date, condition, qty_delta, created_at)
        SELECT a.asset_id, 'checkout', a.employee_id, a.issued_by, a.notes,
               a.expected_return_date, {CONDITION_CASE.format(col='a.condition_out')},
               0, a.issued_at
          FROM asset_assignments a
         ORDER BY a.issued_at, a.id
    """)

    # --- and a checkin for each one that came back -------------------------
    # The store room is the destination: the old table recorded that something
    # was returned but never where it was put, so this is the honest default
    # rather than an invented shelf.
    op.execute(f"""
        INSERT INTO inv_movements
            (item_id, movement_type, from_holder_id, to_location_id, performed_by,
             condition, recovery_amount, qty_delta, created_at)
        SELECT a.asset_id, 'checkin', a.employee_id,
               (SELECT id FROM inv_locations WHERE code = 'STORE'),
               a.received_by, {CONDITION_CASE.format(col='a.condition_in')},
               a.recovery_amount, 0, a.returned_at
          FROM asset_assignments a
         WHERE a.returned_at IS NOT NULL
         ORDER BY a.returned_at, a.id
    """)

    # --- history is never edited -------------------------------------------
    op.execute("""
        CREATE OR REPLACE FUNCTION inv_movements_are_append_only()
        RETURNS TRIGGER AS $$
        BEGIN
          RAISE EXCEPTION
            'inv_movements is append only. Record an audit_adjust movement '
            'instead of changing history.';
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER trg_inv_movements_append_only
        BEFORE UPDATE OR DELETE ON inv_movements
        FOR EACH ROW EXECUTE FUNCTION inv_movements_are_append_only();
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_inv_movements_append_only ON inv_movements")
    op.execute("DROP FUNCTION IF EXISTS inv_movements_are_append_only()")
    op.drop_table('inv_movements')
