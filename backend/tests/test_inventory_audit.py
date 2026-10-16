"""
Cycle counting.

The arithmetic is simple; the judgement is not. What counts as a variance, what
is tolerated, what "not located" means when the thing is on somebody's bench,
and when a repeatedly missing item stops being missing and becomes lost.
"""
from app.models.inventory import (AuditOutcomeEnum, AuditSessionStatusEnum,
                                  InvAuditSession, ItemStatusEnum)


def _open(env, **kw):
    r = env.post("custodian", "/api/v1/inventory/audit/sessions", kw)
    assert r.status_code == 201, r.text
    return r.json()


def _close(env, session_id, **kw):
    r = env.post("custodian",
                 f"/api/v1/inventory/audit/sessions/{session_id}/close", kw)
    assert r.status_code == 200, r.text
    return r.json()


def test_a_session_snapshots_what_it_expects_to_find(env, item, stock):
    session = _open(env, location_id=env.ids["loc_store"])
    tags = [l["item_tag"] for l in session["lines"]]
    # The scope is the store room, so the bin in Lab 1 is not in it.
    assert tags == [item["tag"]]
    assert session["status"] == AuditSessionStatusEnum.OPEN.value
    assert session["opened_by_name"] == "Kamran Custodian"


def test_a_session_scoped_to_a_parent_covers_its_children(env, item, stock):
    """A rack means its shelves; scoping to the site means everything on it."""
    session = _open(env, location_id=env.ids["loc_site"])
    tags = sorted(l["item_tag"] for l in session["lines"])
    assert tags == sorted([item["tag"], stock["tag"]])


def test_a_session_needs_a_scope(env):
    r = env.post("custodian", "/api/v1/inventory/audit/sessions", {})
    assert r.status_code == 400
    assert "location or a category" in r.json()["detail"]


def test_scanning_everything_closes_with_no_variance(env, item):
    session = _open(env, location_id=env.ids["loc_store"])
    r = env.post("custodian",
                 f"/api/v1/inventory/audit/sessions/{session['id']}/scan",
                 {"tag": item["tag"]})
    assert r.status_code == 200, r.text
    assert r.json()["outcome"] == AuditOutcomeEnum.PRESENT.value

    result = _close(env, session["id"])
    assert result["variances"] == 0
    assert result["variance_value"] == 0.0


def test_an_unscanned_item_becomes_a_variance_worth_something(env, item):
    session = _open(env, location_id=env.ids["loc_store"])
    result = _close(env, session["id"])
    assert result["variances"] == 1
    # Valued at replacement cost, which is what the fixture set.
    assert result["variance_value"] == 95000.0


def test_something_on_a_bench_is_not_missing(env, item):
    """
    An item held by a person is exactly where the register says it is. Counting
    it as missing would teach the custodian to ignore the variance report.
    """
    env.post("custodian", f"/api/v1/inventory/items/{item['id']}/checkout",
             {"employee_id": env.ids["eng"]})
    session = _open(env, category_id=env.ids["cat_equipment"])
    result = _close(env, session["id"])
    assert result["variances"] == 0

    db = env.session()
    try:
        row = db.query(InvAuditSession).filter(
            InvAuditSession.id == session["id"]).one()
        line = row.lines[0]
        assert line.outcome == AuditOutcomeEnum.WITH_PERSON.value
        assert "Imran Engineer" in line.note
    finally:
        db.close()


def test_a_tag_found_on_the_wrong_shelf_is_a_result_not_an_error(env, stock):
    session = _open(env, location_id=env.ids["loc_store"])
    assert session["lines"] == []
    r = env.post("custodian",
                 f"/api/v1/inventory/audit/sessions/{session['id']}/scan",
                 {"tag": stock["tag"], "counted_qty": 5000})
    assert r.status_code == 200, r.text
    assert r.json()["outcome"] == AuditOutcomeEnum.FOUND_ELSEWHERE.value


def test_an_unknown_tag_is_a_404(env):
    session = _open(env, location_id=env.ids["loc_store"])
    r = env.post("custodian",
                 f"/api/v1/inventory/audit/sessions/{session['id']}/scan",
                 {"tag": "QGT-EQ-9999"})
    assert r.status_code == 404


def test_a_small_drift_on_passives_is_noted_and_ignored(env, stock):
    """
    Class C tolerance. Chasing an exact count on a reel of resistors costs more
    than the resistors, and a process that demands it is one people stop
    following.
    """
    session = _open(env, location_id=env.ids["loc_lab1"])
    # 4800 against an expected 5000 is a 4% drift, inside the 10% band.
    env.post("custodian", f"/api/v1/inventory/audit/sessions/{session['id']}/scan",
             {"tag": stock["tag"], "counted_qty": 4800})
    result = _close(env, session["id"])
    assert result["variances"] == 0


def test_a_large_drift_on_passives_is_a_variance(env, stock):
    session = _open(env, location_id=env.ids["loc_lab1"])
    env.post("custodian", f"/api/v1/inventory/audit/sessions/{session['id']}/scan",
             {"tag": stock["tag"], "counted_qty": 3000})
    result = _close(env, session["id"])
    assert result["variances"] == 1
    assert result["variance_value"] == 800.0  # 2000 short at 0.4 each


def test_two_consecutive_misses_make_it_lost(env, item):
    """
    The two-strike rule. One missed count is a search; two is a fact.
    """
    first = _open(env, location_id=env.ids["loc_store"])
    result = _close(env, first["id"])
    assert result["marked_lost"] == []
    assert env.get("custodian", f"/api/v1/inventory/items/{item['id']}"
                   ).json()["status"] == ItemStatusEnum.IN_STOCK.value

    second = _open(env, location_id=env.ids["loc_store"])
    result = _close(env, second["id"])
    assert result["marked_lost"] == [item["tag"]]
    assert env.get("custodian", f"/api/v1/inventory/items/{item['id']}"
                   ).json()["status"] == ItemStatusEnum.LOST.value

    kinds = [m["movement_type"] for m in env.get(
        "custodian", f"/api/v1/inventory/items/{item['id']}/movements").json()]
    assert "lost" in kinds


def test_finding_it_between_counts_resets_the_clock(env, item):
    _close(env, _open(env, location_id=env.ids["loc_store"])["id"])
    second = _open(env, location_id=env.ids["loc_store"])
    env.post("custodian", f"/api/v1/inventory/audit/sessions/{second['id']}/scan",
             {"tag": item["tag"]})
    assert _close(env, second["id"])["marked_lost"] == []

    third = _open(env, location_id=env.ids["loc_store"])
    assert _close(env, third["id"])["marked_lost"] == []


def test_a_closed_session_cannot_be_scanned_or_closed_again(env, item):
    session = _open(env, location_id=env.ids["loc_store"])
    _close(env, session["id"])
    r = env.post("custodian",
                 f"/api/v1/inventory/audit/sessions/{session['id']}/scan",
                 {"tag": item["tag"]})
    assert r.status_code == 400
    r = env.post("custodian",
                 f"/api/v1/inventory/audit/sessions/{session['id']}/close", {})
    assert r.status_code == 400


def test_only_a_custodian_runs_a_count(env):
    r = env.post("eng", "/api/v1/inventory/audit/sessions",
                 {"location_id": env.ids["loc_store"]})
    assert r.status_code == 403


def test_a_variance_tells_the_custodian(env, item):
    session = _open(env, location_id=env.ids["loc_store"])
    _close(env, session["id"])
    db = env.session()
    try:
        from app.models.notification import Notification
        titles = [n.title for n in db.query(Notification).filter(
            Notification.user_id == env.ids["custodian"]).all()]
    finally:
        db.close()
    assert any("Audit variance" in t for t in titles)
