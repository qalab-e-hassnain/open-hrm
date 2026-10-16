"""
Custody: checking out, checking in, transferring, and the invariants that hold
while all three happen.

The interesting failures here are not arithmetic. They are the item and its own
history disagreeing about who has it, a guard letting the wrong person move
something, and — the one that matters most — history being rewritten.
"""
from datetime import date, timedelta

import pytest

from app.models.inventory import (AppendOnlyViolation, InvItem, InvMovement,
                                  ItemStatusEnum)


def test_new_item_is_on_a_shelf_with_a_generated_tag(env, item):
    assert item["tag"] == "QGT-EQ-0001"
    assert item["status"] == ItemStatusEnum.IN_STOCK.value
    assert item["location_code"] == "STORE"
    assert item["current_holder_id"] is None


def test_checkout_moves_custody_and_writes_one_movement(env, item):
    r = env.post("eng", f"/api/v1/inventory/items/{item['id']}/checkout")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["current_holder_id"] == env.ids["eng"]
    assert body["status"] == ItemStatusEnum.ISSUED.value
    # The shelf is vacated, not kept alongside the holder.
    assert body["current_location_id"] is None
    # Default hold, because a hold with no date never becomes overdue.
    assert body["hold_due_date"] == str(date.today() + timedelta(days=30))

    history = env.get("eng", f"/api/v1/inventory/items/{item['id']}/movements").json()
    checkouts = [m for m in history if m["movement_type"] == "checkout"]
    assert len(checkouts) == 1
    assert checkouts[0]["to_holder_id"] == env.ids["eng"]
    assert checkouts[0]["from_holder_id"] is None


def test_an_item_cannot_be_held_by_two_people(env, item):
    env.post("eng", f"/api/v1/inventory/items/{item['id']}/checkout")
    r = env.post("eng2", f"/api/v1/inventory/items/{item['id']}/checkout")
    assert r.status_code == 400
    assert "already with" in r.json()["detail"]


def test_checking_out_to_somebody_else_needs_a_custodian(env, item):
    r = env.post("eng", f"/api/v1/inventory/items/{item['id']}/checkout",
                 {"employee_id": env.ids["eng2"]})
    assert r.status_code == 403

    r = env.post("custodian", f"/api/v1/inventory/items/{item['id']}/checkout",
                 {"employee_id": env.ids["eng2"]})
    assert r.status_code == 200
    assert r.json()["current_holder_id"] == env.ids["eng2"]


def test_checkin_returns_it_to_a_named_shelf(env, item):
    env.post("eng", f"/api/v1/inventory/items/{item['id']}/checkout")
    r = env.post("eng", f"/api/v1/inventory/items/{item['id']}/checkin",
                 {"location_id": env.ids["loc_lab1"]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["current_holder_id"] is None
    assert body["location_code"] == "L1-R1-S1-B01"
    assert body["status"] == ItemStatusEnum.IN_STOCK.value
    assert body["hold_due_date"] is None


def test_something_returned_faulty_goes_to_repair_not_to_stock(env, item):
    env.post("eng", f"/api/v1/inventory/items/{item['id']}/checkout")
    r = env.post("eng", f"/api/v1/inventory/items/{item['id']}/checkin",
                 {"location_id": env.ids["loc_store"], "condition": "faulty"})
    assert r.json()["status"] == ItemStatusEnum.UNDER_REPAIR.value


def test_only_admin_or_hr_can_put_a_charge_on_a_return(env, item):
    env.post("custodian", f"/api/v1/inventory/items/{item['id']}/checkout",
             {"employee_id": env.ids["eng"]})
    r = env.post("custodian", f"/api/v1/inventory/items/{item['id']}/checkin",
                 {"location_id": env.ids["loc_store"], "condition": "faulty",
                  "recovery_amount": 5000})
    assert r.status_code == 403
    r = env.post("hr", f"/api/v1/inventory/items/{item['id']}/checkin",
                 {"location_id": env.ids["loc_store"], "condition": "faulty",
                  "recovery_amount": 5000})
    assert r.status_code == 200


def test_transfer_closes_one_hold_and_opens_another(env, item):
    env.post("eng", f"/api/v1/inventory/items/{item['id']}/checkout")
    r = env.post("eng", f"/api/v1/inventory/items/{item['id']}/transfer",
                 {"to_employee_id": env.ids["eng2"], "reason": "Handing over the rig"})
    assert r.status_code == 200, r.text
    assert r.json()["current_holder_id"] == env.ids["eng2"]

    history = env.get("eng", f"/api/v1/inventory/items/{item['id']}/movements").json()
    transfer = next(m for m in history if m["movement_type"] == "transfer")
    assert transfer["from_holder_id"] == env.ids["eng"]
    assert transfer["to_holder_id"] == env.ids["eng2"]

    # The clock belongs to whoever holds it now.
    assert r.json()["days_held"] == 0
    mine = env.get("eng", "/api/v1/inventory/me/holdings").json()
    assert mine == []


def test_a_bystander_cannot_transfer_somebody_elses_kit(env, item):
    env.post("eng", f"/api/v1/inventory/items/{item['id']}/checkout")
    r = env.post("eng2", f"/api/v1/inventory/items/{item['id']}/transfer",
                 {"to_employee_id": env.ids["eng2"]})
    assert r.status_code == 403


def test_renewing_a_hold_pushes_the_date_without_moving_custody(env, item):
    env.post("eng", f"/api/v1/inventory/items/{item['id']}/checkout",
             {"expected_return_date": str(date.today() + timedelta(days=1))})
    r = env.post("eng", f"/api/v1/inventory/items/{item['id']}/renew", {"days": 14})
    assert r.status_code == 200
    assert r.json()["hold_due_date"] == str(date.today() + timedelta(days=14))
    assert r.json()["current_holder_id"] == env.ids["eng"]


def test_a_stock_line_is_consumed_not_checked_out(env, stock):
    r = env.post("eng", f"/api/v1/inventory/items/{stock['id']}/checkout")
    assert r.status_code == 400
    assert "stock line" in r.json()["detail"]

    r = env.post("custodian", f"/api/v1/inventory/items/{stock['id']}/consume",
                 {"qty": 250, "project_id": 7})
    assert r.status_code == 200, r.text
    assert r.json()["qty"] == 4750


def test_consuming_more_than_the_shelf_holds_is_refused(env, stock):
    r = env.post("custodian", f"/api/v1/inventory/items/{stock['id']}/consume",
                 {"qty": 99999, "project_id": 7})
    assert r.status_code == 400
    assert "on the shelf" in r.json()["detail"]


def test_client_hardware_cannot_be_mixed_into_our_stock(env):
    r = env.post("custodian", "/api/v1/inventory/items", {
        "category_id": env.ids["cat_client"],
        "name": "Kora reference unit",
        "ownership": "client",
        "client_name": "Kora",
        "current_location_id": env.ids["loc_client_shelf"]})
    assert r.status_code == 201, r.text
    client_item = r.json()

    env.post("custodian", f"/api/v1/inventory/items/{client_item['id']}/checkout",
             {"employee_id": env.ids["eng"]})
    r = env.post("custodian", f"/api/v1/inventory/items/{client_item['id']}/checkin",
                 {"location_id": env.ids["loc_store"]})
    assert r.status_code == 400
    assert "client shelf" in r.json()["detail"]


def test_our_kit_cannot_be_put_on_a_client_shelf(env, item):
    env.post("eng", f"/api/v1/inventory/items/{item['id']}/checkout")
    r = env.post("eng", f"/api/v1/inventory/items/{item['id']}/checkin",
                 {"location_id": env.ids["loc_client_shelf"]})
    assert r.status_code == 400
    assert "client shelf" in r.json()["detail"]


def test_shipping_to_a_client_requires_a_courier_reference(env, item):
    r = env.post("lead", f"/api/v1/inventory/items/{item['id']}/ship-to-client",
                 {"reference": ""})
    assert r.status_code == 422  # rejected by the schema before it reaches the rule

    r = env.post("eng", f"/api/v1/inventory/items/{item['id']}/ship-to-client",
                 {"reference": "TCS-4471"})
    assert r.status_code == 403  # an engineer does not ship

    r = env.post("lead", f"/api/v1/inventory/items/{item['id']}/ship-to-client",
                 {"reference": "TCS-4471"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == ItemStatusEnum.IN_TRANSIT.value


def test_in_transit_is_allowed_to_have_neither_shelf_nor_holder(env, item):
    env.post("lead", f"/api/v1/inventory/items/{item['id']}/ship-to-client",
             {"reference": "TCS-4471"})
    body = env.get("eng", f"/api/v1/inventory/items/{item['id']}").json()
    assert body["current_location_id"] is None
    assert body["current_holder_id"] is None
    assert body["status"] == ItemStatusEnum.IN_TRANSIT.value


def test_a_scrapped_item_keeps_its_tag_forever(env, item):
    r = env.post("admin", f"/api/v1/inventory/items/{item['id']}/scrap",
                 {"reason": "Dropped, beyond economical repair"})
    assert r.status_code == 200
    assert r.json()["status"] == ItemStatusEnum.SCRAPPED.value

    r = env.post("custodian", "/api/v1/inventory/items", {
        "category_id": env.ids["cat_equipment"], "name": "Replacement scope",
        "tag": "QGT-EQ-0001", "current_location_id": env.ids["loc_store"]})
    assert r.status_code == 400
    assert "already in use" in r.json()["detail"]


def test_a_custodian_cannot_scrap_on_their_own(env, item):
    r = env.post("custodian", f"/api/v1/inventory/items/{item['id']}/scrap",
                 {"reason": "Beyond repair"})
    assert r.status_code == 403


def test_lost_then_found_closes_the_loss_rather_than_erasing_it(env, item):
    env.post("admin", f"/api/v1/inventory/items/{item['id']}/lost",
             {"reason": "Not on the rack at the March count"})
    r = env.post("custodian", f"/api/v1/inventory/items/{item['id']}/found",
                 {"location_id": env.ids["loc_lab1"]})
    assert r.status_code == 200
    assert r.json()["status"] == ItemStatusEnum.IN_STOCK.value

    kinds = [m["movement_type"] for m in
             env.get("eng", f"/api/v1/inventory/items/{item['id']}/movements").json()]
    assert "lost" in kinds and "found" in kinds


def test_nothing_in_the_register_can_be_deleted(env, item):
    """
    Registering writes an intake movement, movements are append-only, so no
    row is ever without history. A mistyped record is edited; a thing that
    should not be on the books is scrapped, which keeps the tag and the trail.
    """
    assert env.delete("custodian",
                      f"/api/v1/inventory/items/{item['id']}").status_code == 405


def test_an_item_must_be_put_somewhere_when_it_is_registered(env):
    """
    The database refuses a row that is neither on a shelf nor with a person,
    so "register it now, find it a home later" is not a state that exists.
    """
    r = env.post("custodian", "/api/v1/inventory/items", {
        "category_id": env.ids["cat_equipment"], "name": "Homeless scope",
        "serial_number": "NOWHERE-1"})
    assert r.status_code == 400
    assert "Say where this is being put" in r.json()["detail"]


def test_registering_writes_an_intake_movement_with_no_origin(env, item):
    history = env.get("custodian",
                      f"/api/v1/inventory/items/{item['id']}/movements").json()
    receive = [m for m in history if m["movement_type"] == "receive"]
    assert len(receive) == 1
    # It came from outside the building, not off the shelf it was put on.
    assert receive[0]["from_location_id"] is None
    assert receive[0]["to_location_id"] == env.ids["loc_store"]


# --- The invariant that matters most --------------------------------------

def test_a_movement_cannot_be_edited(env, item):
    """
    The ORM half of the append-only guarantee.

    Postgres has a trigger that enforces this against psql and against code
    nobody has written yet; this is the half a SQLite test run can see, and
    without it the invariant would be completely untested.
    """
    env.post("eng", f"/api/v1/inventory/items/{item['id']}/checkout")
    db = env.session()
    try:
        movement = db.query(InvMovement).first()
        movement.reason = "actually it was fine"
        with pytest.raises(AppendOnlyViolation):
            db.commit()
    finally:
        db.rollback()
        db.close()


def test_a_movement_cannot_be_deleted(env, item):
    env.post("eng", f"/api/v1/inventory/items/{item['id']}/checkout")
    db = env.session()
    try:
        db.delete(db.query(InvMovement).first())
        with pytest.raises(AppendOnlyViolation):
            db.commit()
    finally:
        db.rollback()
        db.close()


def test_a_correction_is_a_new_movement(env, stock):
    r = env.post("custodian", f"/api/v1/inventory/items/{stock['id']}/adjust",
                 {"qty_delta": -12, "reason": "Miscounted at intake"})
    assert r.status_code == 200
    assert r.json()["qty"] == 4988
    history = env.get("custodian",
                      f"/api/v1/inventory/items/{stock['id']}/movements").json()
    adjust = next(m for m in history if m["movement_type"] == "audit_adjust")
    assert adjust["reason"] == "Miscounted at intake"
    assert adjust["qty_delta"] == -12


def test_an_adjustment_without_a_reason_is_refused(env, stock):
    r = env.post("custodian", f"/api/v1/inventory/items/{stock['id']}/adjust",
                 {"qty_delta": -1, "reason": ""})
    assert r.status_code == 422


def test_the_register_never_disagrees_with_its_own_log(env, item):
    """Walk an item through its life and check the two stay in step."""
    env.post("eng", f"/api/v1/inventory/items/{item['id']}/checkout")
    env.post("eng", f"/api/v1/inventory/items/{item['id']}/transfer",
             {"to_employee_id": env.ids["eng2"]})
    env.post("eng2", f"/api/v1/inventory/items/{item['id']}/checkin",
             {"location_id": env.ids["loc_lab1"]})

    db = env.session()
    try:
        row = db.query(InvItem).filter(InvItem.id == item["id"]).one()
        latest = (db.query(InvMovement)
                  .filter(InvMovement.item_id == row.id)
                  .order_by(InvMovement.created_at.desc(),
                            InvMovement.id.desc()).first())
        assert row.current_holder_id == latest.to_holder_id
        assert row.current_location_id == latest.to_location_id
        assert row.status == ItemStatusEnum.IN_STOCK.value
    finally:
        db.close()


# --- Redaction ------------------------------------------------------------

def test_an_engineer_sees_the_catalogue_but_not_what_it_cost(env, item):
    mine = env.get("eng", f"/api/v1/inventory/items/{item['id']}").json()
    assert mine["name"] == "Rigol DS1054Z oscilloscope"
    assert mine["purchase_cost"] is None
    assert mine["replacement_cost"] is None

    theirs = env.get("custodian", f"/api/v1/inventory/items/{item['id']}").json()
    assert theirs["purchase_cost"] == 85000.0
