"""
The offboarding hook — the reason the module lives inside the HRM at all.

Section 7 calls this "the reason for the whole approach" and expects it to
recover more hardware than every other feature combined. It only works if the
gate reads the same register the lab writes to, which is why there is one
register rather than two; the first test here is really a test of that
decision.
"""
from datetime import date, timedelta

from app.models.settlement import FinalSettlement
from app.utils import settlement as settlement_utils


def _resignation_awaiting_clearance(env, who="eng"):
    """Walk a resignation up to the point where HR has to clear it."""
    last_day = (date.today() + timedelta(days=30)).isoformat()
    r = env.post(who, "/api/v1/offboarding/resignations",
                 {"reason": "Moving on to something new",
                  "proposed_last_working_day": last_day})
    assert r.status_code in (200, 201), r.text
    res_id = r.json()["id"]
    env.client.put(f"/api/v1/offboarding/resignations/{res_id}/decide",
                   json={"approved": True}, headers=env.hdr("lead"))
    env.client.put(f"/api/v1/offboarding/resignations/{res_id}/decide",
                   json={"approved": True}, headers=env.hdr("admin"))
    return res_id


def _clear_every_box(env, res_id):
    detail = env.get("admin", "/api/v1/offboarding/resignations").json()
    row = next(r for r in detail if r["id"] == res_id)
    for item in row["clearance_items"]:
        env.client.put(f"/api/v1/offboarding/clearance/{item['id']}",
                       json={"status": "completed"}, headers=env.hdr("admin"))
    return row


def _try_to_clear(env, res_id):
    return env.client.put(f"/api/v1/offboarding/resignations/{res_id}/decide",
                          json={"approved": True}, headers=env.hdr("admin"))


def test_lab_equipment_reaches_the_clearance_list(env, item):
    """
    A lab instrument, not an office laptop, and the gate still sees it.

    If the inventory had landed in its own tables next to the old asset
    register, this test would fail and the decisive feature would be shipping
    half-blind.
    """
    env.post("custodian", f"/api/v1/inventory/items/{item['id']}/checkout",
             {"employee_id": env.ids["eng"]})
    res_id = _resignation_awaiting_clearance(env)
    row = _clear_every_box(env, res_id)
    labels = [i["label"] for i in row["clearance_items"]]
    assert any("Rigol DS1054Z oscilloscope (QGT-EQ-0001)" in l for l in labels)


def test_every_box_ticked_is_not_enough_while_the_kit_is_out(env, item):
    env.post("custodian", f"/api/v1/inventory/items/{item['id']}/checkout",
             {"employee_id": env.ids["eng"]})
    res_id = _resignation_awaiting_clearance(env)
    _clear_every_box(env, res_id)

    r = _try_to_clear(env, res_id)
    assert r.status_code == 400
    assert "still issued" in r.json()["detail"]
    assert "QGT-EQ-0001" in r.json()["detail"]


def test_returning_the_item_unblocks_the_exit(env, item):
    env.post("custodian", f"/api/v1/inventory/items/{item['id']}/checkout",
             {"employee_id": env.ids["eng"]})
    res_id = _resignation_awaiting_clearance(env)
    _clear_every_box(env, res_id)

    env.post("custodian", f"/api/v1/inventory/items/{item['id']}/checkin",
             {"location_id": env.ids["loc_store"]})
    r = _try_to_clear(env, res_id)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "cleared"


def test_transferring_the_item_also_unblocks_the_exit(env, item):
    """
    The second of the three ways section 7 says a line may be closed.

    Handing the rig to whoever is picking the work up is a perfectly good
    answer to "where is it", and before this the only way to record it was a
    return that never happened followed by a checkout that did not either.
    """
    env.post("custodian", f"/api/v1/inventory/items/{item['id']}/checkout",
             {"employee_id": env.ids["eng"]})
    res_id = _resignation_awaiting_clearance(env)
    _clear_every_box(env, res_id)

    r = env.post("custodian", f"/api/v1/inventory/items/{item['id']}/transfer",
                 {"to_employee_id": env.ids["eng2"], "reason": "Taking over the work"})
    assert r.status_code == 200, r.text
    assert _try_to_clear(env, res_id).status_code == 200
    assert env.get("eng2", "/api/v1/inventory/me/holdings").json()[0]["tag"] == "QGT-EQ-0001"


def test_a_write_off_unblocks_the_exit_and_charges_for_it(env, item):
    """
    The third way, and the only one that closes a line without the hardware
    coming back. Admin only, reason mandatory, and the amount lands on the
    settlement — so it is a decision with a number attached rather than a
    checkbox somebody ticked to get an exit moving.
    """
    env.post("custodian", f"/api/v1/inventory/items/{item['id']}/checkout",
             {"employee_id": env.ids["eng"]})
    res_id = _resignation_awaiting_clearance(env)
    _clear_every_box(env, res_id)

    refused = env.post("custodian", f"/api/v1/inventory/items/{item['id']}/write-off",
                       {"reason": "Never came back", "recovery_amount": 95000})
    assert refused.status_code == 403

    r = env.post("admin", f"/api/v1/inventory/items/{item['id']}/write-off",
                 {"reason": "Not returned at exit", "recovery_amount": 95000})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "lost"
    assert _try_to_clear(env, res_id).status_code == 200

    db = env.session()
    try:
        assert settlement_utils._asset_recovery(db, env.ids["eng"]) == 95000.0
    finally:
        db.close()


def test_a_write_off_needs_a_reason(env, item):
    env.post("custodian", f"/api/v1/inventory/items/{item['id']}/checkout",
             {"employee_id": env.ids["eng"]})
    r = env.post("admin", f"/api/v1/inventory/items/{item['id']}/write-off",
                 {"reason": "", "recovery_amount": 1000})
    assert r.status_code == 422


def test_a_damaged_return_also_reaches_the_settlement(env, item):
    env.post("custodian", f"/api/v1/inventory/items/{item['id']}/checkout",
             {"employee_id": env.ids["eng"]})
    env.post("hr", f"/api/v1/inventory/items/{item['id']}/checkin",
             {"location_id": env.ids["loc_store"], "condition": "faulty",
              "recovery_amount": 12000})
    db = env.session()
    try:
        assert settlement_utils._asset_recovery(db, env.ids["eng"]) == 12000.0
    finally:
        db.close()


def test_nothing_is_charged_for_kit_that_is_merely_outstanding(env, item):
    """
    Unreturned equipment blocks clearance rather than being deducted. That is
    a conversation, not a silent charge on somebody's final pay.
    """
    env.post("custodian", f"/api/v1/inventory/items/{item['id']}/checkout",
             {"employee_id": env.ids["eng"]})
    db = env.session()
    try:
        assert settlement_utils._asset_recovery(db, env.ids["eng"]) == 0.0
    finally:
        db.close()


def test_an_exit_with_nothing_held_clears_straight_through(env):
    res_id = _resignation_awaiting_clearance(env)
    _clear_every_box(env, res_id)
    assert _try_to_clear(env, res_id).status_code == 200


def test_a_holder_can_see_their_own_list_and_a_bystander_cannot(env, item):
    env.post("custodian", f"/api/v1/inventory/items/{item['id']}/checkout",
             {"employee_id": env.ids["eng"]})
    assert len(env.get("eng", "/api/v1/inventory/me/holdings").json()) == 1
    # Their lead may look, because a manager signing off a handover needs to.
    assert env.get(
        "lead", f"/api/v1/inventory/employees/{env.ids['eng']}/holdings"
    ).status_code == 200
    # A peer may not.
    assert env.get(
        "eng2", f"/api/v1/inventory/employees/{env.ids['eng']}/holdings"
    ).status_code == 403
