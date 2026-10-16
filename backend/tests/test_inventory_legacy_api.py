"""
The deprecated /api/v1/assets endpoints, served out of the new tables.

The portal still calls these, and it must not have to move in the same deploy
as the backend. Two things are being checked: that the old shapes and
vocabulary come back unchanged, and that the issue-and-return pairs the old
table stored as one row can be reassembled from an append-only log.

This is also the closest a SQLite test run can get to the Phase 2 backfill.
The migration's SQL goes the other way — pairs into events — and can only be
verified against Postgres; this verifies the inverse, which is the same
mapping read backwards.
"""
from datetime import date, timedelta

from app.models.inventory import InvItem, ItemStatusEnum


def _create(env, **extra):
    payload = {"tag": "QG-LT-001", "category": "laptop", "name": "Dell Latitude 5420",
               "serial_number": "ABC123", "replacement_cost": 180000}
    payload.update(extra)
    r = env.post("admin", "/api/v1/assets", payload)
    assert r.status_code == 201, r.text
    return r.json()


def test_the_old_shape_comes_back_unchanged(env):
    asset = _create(env)
    assert asset["tag"] == "QG-LT-001"
    assert asset["category"] == "laptop"
    assert asset["status"] == "in_stock"
    assert asset["condition"] == "good"
    assert asset["held_by_id"] is None


def test_responses_are_marked_deprecated(env):
    r = env.client.get("/api/v1/assets", headers=env.hdr("admin"))
    assert r.status_code == 200
    assert r.headers.get("Deprecation") == "true"
    assert "/api/v1/inventory" in r.headers.get("Link", "")


def test_issue_and_return_round_trip_in_the_old_vocabulary(env):
    asset = _create(env)
    r = env.post("admin", f"/api/v1/assets/{asset['id']}/issue",
                 {"employee_id": env.ids["eng"], "condition_out": "new"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "issued"
    assert r.json()["held_by_id"] == env.ids["eng"]

    mine = env.get("eng", "/api/v1/assets/me").json()
    assert len(mine) == 1
    assert mine[0]["asset_tag"] == "QG-LT-001"
    assert mine[0]["condition_out"] == "new"
    assert mine[0]["returned_at"] is None

    r = env.post("admin", f"/api/v1/assets/{asset['id']}/return",
                 {"condition_in": "damaged", "recovery_amount": 15000})
    assert r.status_code == 200, r.text
    # Damaged, so it goes to repair — which the old vocabulary calls in_repair.
    assert r.json()["status"] == "in_repair"
    assert r.json()["condition"] == "damaged"
    assert env.get("eng", "/api/v1/assets/me").json() == []


def test_history_reassembles_the_pairs(env):
    asset = _create(env)
    env.post("admin", f"/api/v1/assets/{asset['id']}/issue",
             {"employee_id": env.ids["eng"], "condition_out": "new"})
    env.post("admin", f"/api/v1/assets/{asset['id']}/return", {"condition_in": "good"})
    env.post("admin", f"/api/v1/assets/{asset['id']}/issue",
             {"employee_id": env.ids["eng2"], "condition_out": "good"})

    history = env.get("admin", f"/api/v1/assets/{asset['id']}/history").json()
    assert len(history) == 2
    open_hold = [h for h in history if h["returned_at"] is None]
    assert len(open_hold) == 1
    assert open_hold[0]["employee_id"] == env.ids["eng2"]

    closed = [h for h in history if h["returned_at"] is not None]
    assert closed[0]["employee_id"] == env.ids["eng"]
    assert closed[0]["condition_in"] == "good"


def test_a_transfer_closes_the_old_style_hold(env):
    """
    A movement type the old table could not express at all, read back through
    the old shape: the first hold ends where the second begins.
    """
    asset = _create(env)
    env.post("admin", f"/api/v1/assets/{asset['id']}/issue",
             {"employee_id": env.ids["eng"]})
    # Office equipment changes hands through admin or HR.
    env.post("admin", f"/api/v1/inventory/items/{asset['id']}/transfer",
             {"to_employee_id": env.ids["eng2"]})

    history = env.get("admin", f"/api/v1/assets/{asset['id']}/history").json()
    assert len(history) == 2
    by_employee = {h["employee_id"]: h for h in history}
    assert by_employee[env.ids["eng"]]["returned_at"] is not None
    assert by_employee[env.ids["eng2"]]["returned_at"] is None


def test_the_old_status_filter_still_selects(env):
    asset = _create(env)
    env.post("admin", f"/api/v1/assets/{asset['id']}/issue",
             {"employee_id": env.ids["eng"]})
    assert len(env.get("admin", "/api/v1/assets?status=issued").json()) == 1
    assert env.get("admin", "/api/v1/assets?status=in_stock").json() == []


def test_the_old_category_filter_still_selects(env):
    _create(env)
    _create(env, tag="QG-MN-001", category="monitor", name="Dell U2412")
    assert len(env.get("admin", "/api/v1/assets?category=laptop").json()) == 1
    assert len(env.get("admin", "/api/v1/assets?category=monitor").json()) == 1


def test_an_unknown_category_is_refused_clearly(env):
    r = env.post("admin", "/api/v1/assets",
                 {"tag": "X-1", "category": "spaceship", "name": "Nope"})
    assert r.status_code == 400
    assert "Unknown category 'spaceship'" in r.json()["detail"]


def test_the_old_update_guards_still_hold(env):
    asset = _create(env)
    r = env.put("admin", f"/api/v1/assets/{asset['id']}", {"status": "issued"})
    assert r.status_code == 400
    assert "Issue the asset" in r.json()["detail"]

    env.post("admin", f"/api/v1/assets/{asset['id']}/issue",
             {"employee_id": env.ids["eng"]})
    r = env.put("admin", f"/api/v1/assets/{asset['id']}", {"status": "retired"})
    assert r.status_code == 400
    assert "Record its return first" in r.json()["detail"]


def test_an_item_created_through_the_old_api_lands_on_a_real_shelf(env):
    """
    The old API never took a location, and the new invariant demands one. The
    store room is the honest default rather than an invented shelf.
    """
    asset = _create(env)
    db = env.session()
    try:
        row = db.query(InvItem).filter(InvItem.id == asset["id"]).one()
        assert row.current_location_id is not None
        assert row.location.code == "STORE"
    finally:
        db.close()


def test_the_old_and_new_apis_see_the_same_item(env):
    """There is one register. That is the whole point of the migration."""
    asset = _create(env)
    env.post("admin", f"/api/v1/assets/{asset['id']}/issue",
             {"employee_id": env.ids["eng"]})

    new_view = env.get("custodian", f"/api/v1/inventory/items/{asset['id']}").json()
    assert new_view["tag"] == "QG-LT-001"
    assert new_view["status"] == ItemStatusEnum.ISSUED.value
    assert new_view["current_holder_id"] == env.ids["eng"]
    assert new_view["category_name"] == "Laptop"
