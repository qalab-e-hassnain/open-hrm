"""
The digest.

Section 13 asks for one message per person rather than one per alert, because
individual alerts train people to ignore the channel. So the thing under test
is mostly the grouping: the right lines reaching the right person, once.
"""
from datetime import date, datetime, timedelta

from app.models.inventory import InvItem
from app.utils import inv_alerts


def _set(env, item_id, **fields):
    db = env.session()
    try:
        db.query(InvItem).filter(InvItem.id == item_id).update(fields)
        db.commit()
    finally:
        db.close()


def _collect(env):
    db = env.session()
    try:
        return {d.user.email: d for d in inv_alerts.collect(db).values()}
    finally:
        db.close()


def test_an_overdue_hold_reaches_the_holder_and_their_lead(env, item):
    env.post("custodian", f"/api/v1/inventory/items/{item['id']}/checkout",
             {"employee_id": env.ids["eng"]})
    _set(env, item["id"], hold_due_date=date.today() - timedelta(days=4))

    digests = _collect(env)
    assert "Overdue — please return or renew" in digests["eng@qgt.test"].sections
    line = digests["eng@qgt.test"].sections["Overdue — please return or renew"][0]
    assert "4 days overdue" in line
    # The lead hears about their own team's, not the whole lab's.
    assert "Your team's overdue equipment" in digests["lead@qgt.test"].sections


def test_a_hold_coming_due_warns_only_the_holder(env, item):
    env.post("custodian", f"/api/v1/inventory/items/{item['id']}/checkout",
             {"employee_id": env.ids["eng"]})
    _set(env, item["id"], hold_due_date=date.today() + timedelta(days=2))

    digests = _collect(env)
    assert "Due back soon" in digests["eng@qgt.test"].sections
    assert "lead@qgt.test" not in digests


def test_a_hold_with_time_left_says_nothing(env, item):
    env.post("custodian", f"/api/v1/inventory/items/{item['id']}/checkout",
             {"employee_id": env.ids["eng"]})
    assert "eng@qgt.test" not in _collect(env)


def test_low_stock_goes_to_the_custodian(env, stock):
    env.post("custodian", f"/api/v1/inventory/items/{stock['id']}/consume",
             {"qty": 4600, "project_id": 1})
    digests = _collect(env)
    assert "Stock below minimum" in digests["store@qgt.test"].sections
    # Admins hear too, so the alert does not vanish before a custodian is named.
    assert "Stock below minimum" in digests["admin@qgt.test"].sections


def test_quarantine_over_the_limit_is_chased(env, item):
    env.post("custodian", f"/api/v1/inventory/items/{item['id']}/checkout",
             {"employee_id": env.ids["eng"]})
    env.post("custodian", f"/api/v1/inventory/items/{item['id']}/checkin",
             {"location_id": env.ids["loc_quarantine"], "condition": "faulty"})
    _set(env, item["id"], quarantined_at=datetime.utcnow() - timedelta(days=45))

    digests = _collect(env)
    assert "In quarantine too long" in digests["store@qgt.test"].sections


def test_leaving_quarantine_stops_the_clock(env, item):
    env.post("custodian", f"/api/v1/inventory/items/{item['id']}/checkout",
             {"employee_id": env.ids["eng"]})
    env.post("custodian", f"/api/v1/inventory/items/{item['id']}/checkin",
             {"location_id": env.ids["loc_quarantine"], "condition": "faulty"})
    _set(env, item["id"], quarantined_at=datetime.utcnow() - timedelta(days=45))
    env.post("custodian", f"/api/v1/inventory/items/{item['id']}/repair-in",
             {"location_id": env.ids["loc_store"]})

    digest = _collect(env).get("store@qgt.test")
    assert digest is None or "In quarantine too long" not in digest.sections


def test_long_leave_with_kit_out_tells_the_custodian(env, item):
    from app.models.leave import Leave, LeaveStatusEnum

    env.post("custodian", f"/api/v1/inventory/items/{item['id']}/checkout",
             {"employee_id": env.ids["eng"]})
    db = env.session()
    try:
        db.add(Leave(employee_id=env.ids["eng"], leave_type="annual",
                     start_date=date.today() + timedelta(days=2),
                     end_date=date.today() + timedelta(days=20),
                     reason="Holiday", status=LeaveStatusEnum.APPROVED))
        db.commit()
    finally:
        db.close()

    digests = _collect(env)
    section = digests["store@qgt.test"].sections
    assert "On long leave, still holding equipment" in section
    assert "QGT-EQ-0001" in section["On long leave, still holding equipment"][0]


def test_short_leave_does_not(env, item):
    from app.models.leave import Leave, LeaveStatusEnum

    env.post("custodian", f"/api/v1/inventory/items/{item['id']}/checkout",
             {"employee_id": env.ids["eng"]})
    db = env.session()
    try:
        db.add(Leave(employee_id=env.ids["eng"], leave_type="annual",
                     start_date=date.today() + timedelta(days=2),
                     end_date=date.today() + timedelta(days=4),
                     reason="Long weekend", status=LeaveStatusEnum.APPROVED))
        db.commit()
    finally:
        db.close()
    assert "store@qgt.test" not in _collect(env)


def test_everything_for_one_person_arrives_as_one_digest(env, item, stock):
    """The whole point: one message, several sections, not several messages."""
    env.post("custodian", f"/api/v1/inventory/items/{stock['id']}/consume",
             {"qty": 4600, "project_id": 1})
    env.post("custodian", f"/api/v1/inventory/items/{item['id']}/checkout",
             {"employee_id": env.ids["custodian"]})
    _set(env, item["id"], hold_due_date=date.today() - timedelta(days=1))

    digests = _collect(env)
    custodian = digests["store@qgt.test"]
    assert len(custodian.sections) >= 2
    assert custodian.line_count >= 2


def test_the_sweep_sends_one_email_per_recipient(env, item, monkeypatch):
    sent = []
    monkeypatch.setattr("app.utils.email.send_email",
                        lambda to, subject, body, **kw: sent.append((to, subject)))
    env.post("custodian", f"/api/v1/inventory/items/{item['id']}/checkout",
             {"employee_id": env.ids["eng"]})
    _set(env, item["id"], hold_due_date=date.today() - timedelta(days=3))

    db = env.session()
    try:
        result = inv_alerts.send_digests(db)
    finally:
        db.close()

    assert result["recipients"] == 2          # the holder and their lead
    assert result["emails_sent"] == 2
    assert len(sent) == 2
    assert all(s[1] == "Lab inventory — your daily digest" for s in sent)


def test_a_quiet_day_sends_nothing(env, item):
    db = env.session()
    try:
        assert inv_alerts.send_digests(db)["recipients"] == 0
    finally:
        db.close()
