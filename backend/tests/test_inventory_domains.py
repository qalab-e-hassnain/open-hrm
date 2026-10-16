"""
Lab and office are separate sections of one register.

The lab custodian runs the lab; office equipment (the old asset register) is
issued by admin or HR, never self-served, and carries no expiring hold.
"""
from datetime import date, timedelta

import pytest

from app.models.inventory import InvItem


@pytest.fixture
def laptop(env):
    r = env.post("admin", "/api/v1/inventory/items", {
        "category_id": env.ids["cat_laptop"],
        "name": "ThinkPad T14",
        "serial_number": "PF-T14-0001",
        "current_location_id": env.ids["loc_store"],
    })
    assert r.status_code == 201, r.text
    assert r.json()["domain"] == "office"
    return r.json()


def _issue_laptop(env, laptop, to="eng"):
    r = env.post("admin", f"/api/v1/inventory/items/{laptop['id']}/checkout",
                 {"employee_id": env.ids[to]})
    assert r.status_code == 200, r.text
    return r.json()


def test_each_section_lists_only_its_own_items(env, item, laptop):
    lab = env.get("eng", "/api/v1/inventory/items", params={"domain": "lab"}).json()
    office = env.get("eng", "/api/v1/inventory/items", params={"domain": "office"}).json()
    assert [i["id"] for i in lab] == [item["id"]]
    assert [i["id"] for i in office] == [laptop["id"]]
    assert {i["domain"] for i in env.get("eng", "/api/v1/inventory/items").json()} == {"lab", "office"}


def test_an_unknown_section_is_refused(env):
    assert env.get("eng", "/api/v1/inventory/items", params={"domain": "garage"}).status_code == 400


def test_categories_filter_by_section(env):
    office = env.get("eng", "/api/v1/inventory/categories", params={"domain": "office"}).json()
    names = {c["name"] for c in office}
    assert "Laptop" in names and "Test and measurement" not in names


def test_the_lab_custodian_cannot_register_office_equipment(env):
    r = env.post("custodian", "/api/v1/inventory/items", {
        "category_id": env.ids["cat_laptop"], "name": "ThinkPad",
        "serial_number": "X1", "current_location_id": env.ids["loc_store"]})
    assert r.status_code == 403


def test_the_lab_custodian_cannot_create_an_office_category(env):
    r = env.post("custodian", "/api/v1/inventory/categories",
                 {"name": "Tablets", "domain": "office"})
    assert r.status_code == 403


def test_nobody_self_serves_a_laptop(env, laptop):
    r = env.post("eng", f"/api/v1/inventory/items/{laptop['id']}/checkout", {})
    assert r.status_code == 403
    scan = env.get("eng", f"/api/v1/inventory/items/by-tag/{laptop['tag']}").json()
    assert scan["actions"] == []


def test_an_issued_laptop_has_no_hold_to_expire(env, laptop):
    issued = _issue_laptop(env, laptop)
    assert issued["hold_due_date"] is None
    assert issued["is_overdue"] is False
    r = env.post("eng", f"/api/v1/inventory/items/{laptop['id']}/renew", {})
    assert r.status_code == 400


def test_a_laptop_comes_back_through_admin_not_its_holder_or_the_lab(env, laptop):
    _issue_laptop(env, laptop)
    back = {"location_id": env.ids["loc_store"], "condition": "working"}
    assert env.post("eng", f"/api/v1/inventory/items/{laptop['id']}/checkin", back).status_code == 403
    assert env.post("custodian", f"/api/v1/inventory/items/{laptop['id']}/checkin", back).status_code == 403
    assert env.post("admin", f"/api/v1/inventory/items/{laptop['id']}/checkin", back).status_code == 200


def test_hr_manages_office_equipment(env, laptop):
    _issue_laptop(env, laptop)
    r = env.post("hr", f"/api/v1/inventory/items/{laptop['id']}/transfer",
                 {"to_employee_id": env.ids["eng2"]})
    assert r.status_code == 200, r.text


def test_lab_kit_is_still_self_service(env, item):
    r = env.post("eng", f"/api/v1/inventory/items/{item['id']}/checkout", {})
    assert r.status_code == 200
    assert r.json()["hold_due_date"] is not None


def test_the_custodians_overdue_report_is_the_lab_only(env, item, laptop):
    _issue_laptop(env, laptop)
    env.post("eng", f"/api/v1/inventory/items/{item['id']}/checkout", {})
    db = env.session()
    try:
        long_ago = date.today() - timedelta(days=5)
        db.query(InvItem).filter(InvItem.id.in_([item["id"], laptop["id"]])).update(
            {"hold_due_date": long_ago}, synchronize_session=False)
        db.commit()
    finally:
        db.close()
    seen = env.get("custodian", "/api/v1/inventory/reports/overdue",
                   params={"domain": "office"}).json()
    assert [i["id"] for i in seen] == [item["id"]]
    assert {i["id"] for i in env.get("admin", "/api/v1/inventory/reports/overdue").json()} \
        == {item["id"], laptop["id"]}


def test_a_leavers_laptop_still_blocks_clearance(env, laptop):
    _issue_laptop(env, laptop)
    held = env.get("admin", f"/api/v1/inventory/employees/{env.ids['eng']}/holdings").json()
    assert [i["id"] for i in held] == [laptop["id"]]


def test_a_lab_count_never_expects_a_laptop(env, item, laptop):
    scope = {"location_id": env.ids["loc_store"]}
    lab = env.post("custodian", "/api/v1/inventory/audit/sessions", scope).json()
    assert [l["item_tag"] for l in lab["lines"]] == [item["tag"]]
    r = env.post("custodian", f"/api/v1/inventory/audit/sessions/{lab['id']}/scan",
                 {"tag": laptop["tag"]})
    assert r.status_code == 400
    full = env.post("admin", "/api/v1/inventory/audit/sessions", scope).json()
    assert {l["item_tag"] for l in full["lines"]} == {item["tag"], laptop["tag"]}


def test_a_laptop_ignores_a_return_date(env, laptop):
    r = env.post("admin", f"/api/v1/inventory/items/{laptop['id']}/checkout",
                 {"employee_id": env.ids["eng"],
                  "expected_return_date": str(date.today() + timedelta(days=3))})
    assert r.json()["hold_due_date"] is None


def test_office_equipment_is_not_requested_through_the_lab(env, laptop):
    by_category = env.post("eng", "/api/v1/inventory/requests",
                           {"category_id": env.ids["cat_laptop"], "qty": 1,
                            "purpose": "New starter needs one"})
    by_item = env.post("eng", "/api/v1/inventory/requests",
                       {"item_id": laptop["id"], "qty": 1, "purpose": "Mine broke, please"})
    assert by_category.status_code == 400 and by_item.status_code == 400
