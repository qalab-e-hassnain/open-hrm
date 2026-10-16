"""
End-to-end walks through the new lifecycle features, against a scratch
database rather than the live one.

These exist because the interesting failures in this area are not arithmetic —
they are a relationship that will not load, a route shadowed by another, a
guard that lets the wrong person approve something. None of that shows up in a
unit test of a pure function.
"""
import calendar
import json
from datetime import date, datetime, time, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.security import create_access_token, get_password_hash
from app.database import Base, get_db
from app.main import app
from app.models.user import RoleEnum, User
from app.utils import scheduler


@pytest.fixture
def env(monkeypatch):
    """A scratch database, three people, and no background sweeps."""
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    # `from app import models`, not `import app.models` — the latter rebinds the
    # name `app` from the FastAPI instance to the package, and every use of the
    # app below then fails with an AttributeError.
    from app import models as _models  # noqa: F401 — registers every table
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    # The traffic hook would otherwise fire on the first request and reach out
    # to the office fingerprint device from a test run.
    monkeypatch.setattr(scheduler, "should_poll", lambda: False)

    db = Session()
    admin = User(email="admin@example.com", hashed_password=get_password_hash("x"),
                 full_name="Ada Admin", role=RoleEnum.ADMIN, is_hr=True,
                 is_active=True, base_salary=200_000, standard_hours=8,
                 date_of_joining=datetime(2020, 1, 1), designation="Director")
    manager = User(email="mgr@example.com", hashed_password=get_password_hash("x"),
                   full_name="Mo Manager", role=RoleEnum.MANAGER, is_active=True,
                   base_salary=150_000, standard_hours=8,
                   date_of_joining=datetime(2021, 6, 1), designation="Team Lead")
    db.add_all([admin, manager])
    db.commit()
    employee = User(email="emp@example.com", hashed_password=get_password_hash("x"),
                    full_name="Eve Employee", role=RoleEnum.EMPLOYEE, is_active=True,
                    base_salary=80_000, standard_hours=8, manager_id=manager.id,
                    # Relative, not a fixed date. Eve has to stay solidly
                    # mid-probation however long after this was written the
                    # suite runs — a hardcoded July put her inside the
                    # fortnight's reminder notice once real time caught up,
                    # and the test started failing on its own.
                    date_of_joining=datetime.combine(
                        date.today() - timedelta(days=30), time.min),
                    designation="Engineer")
    db.add(employee)
    db.commit()
    # The asset endpoints now resolve categories by name and need somewhere to
    # put a returned item; both come from the inv01 migration in production and
    # from here in a create_all test database.
    from tests.conftest import seed_inventory_reference
    seed_inventory_reference(db)

    ids = {"admin": admin.id, "manager": manager.id, "employee": employee.id}

    def override():
        session = Session()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override
    client = TestClient(app)

    def auth(who):
        email = {"admin": "admin@example.com", "manager": "mgr@example.com",
                 "employee": "emp@example.com"}[who]
        return {"Authorization": f"Bearer {create_access_token({'sub': email})}"}

    yield {"client": client, "auth": auth, "ids": ids, "db": db}

    app.dependency_overrides.clear()
    db.close()


class TestAssetRegister:
    def test_issue_and_return_round_trip(self, env):
        c, auth, ids = env["client"], env["auth"], env["ids"]

        r = c.post("/api/v1/assets", headers=auth("admin"), json={
            "tag": "QG-LT-001", "category": "laptop", "name": "Dell Latitude 5420",
            "serial_number": "ABC123", "replacement_cost": 180000})
        assert r.status_code == 201, r.text
        asset_id = r.json()["id"]
        assert r.json()["status"] == "in_stock"

        r = c.post(f"/api/v1/assets/{asset_id}/issue", headers=auth("admin"),
                   json={"employee_id": ids["employee"], "condition_out": "new"})
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "issued"
        assert r.json()["held_by_name"] == "Eve Employee"

        # The holder can see it without any special rights.
        mine = c.get("/api/v1/assets/me", headers=auth("employee")).json()
        assert [a["asset_tag"] for a in mine] == ["QG-LT-001"]

        r = c.post(f"/api/v1/assets/{asset_id}/return", headers=auth("admin"),
                   json={"condition_in": "damaged", "status_after": "in_repair",
                         "recovery_amount": 5000})
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "in_repair"
        assert r.json()["held_by_id"] is None
        assert c.get("/api/v1/assets/me", headers=auth("employee")).json() == []

    def test_the_same_asset_cannot_be_issued_twice(self, env):
        c, auth, ids = env["client"], env["auth"], env["ids"]
        asset_id = c.post("/api/v1/assets", headers=auth("admin"), json={
            "tag": "QG-LT-002", "category": "laptop", "name": "MacBook"}).json()["id"]
        c.post(f"/api/v1/assets/{asset_id}/issue", headers=auth("admin"),
               json={"employee_id": ids["employee"]})
        r = c.post(f"/api/v1/assets/{asset_id}/issue", headers=auth("admin"),
                   json={"employee_id": ids["manager"]})
        assert r.status_code == 400
        assert "Eve Employee" in r.json()["detail"]

    def test_an_employee_cannot_read_the_register(self, env):
        c, auth = env["client"], env["auth"]
        # Purchase costs are on this list.
        assert c.get("/api/v1/assets", headers=auth("employee")).status_code == 403

    def test_an_asset_with_history_is_retired_not_deleted(self, env):
        c, auth, ids = env["client"], env["auth"], env["ids"]
        asset_id = c.post("/api/v1/assets", headers=auth("admin"), json={
            "tag": "QG-PH-001", "category": "phone", "name": "Pixel"}).json()["id"]
        c.post(f"/api/v1/assets/{asset_id}/issue", headers=auth("admin"),
               json={"employee_id": ids["employee"]})
        r = c.delete(f"/api/v1/assets/{asset_id}", headers=auth("admin"))
        assert r.status_code == 400
        assert "retired" in r.json()["detail"]


class TestLoans:
    def _approved_loan(self, env, principal=60_000, count=3):
        c, auth, ids = env["client"], env["auth"], env["ids"]
        loan_id = c.post("/api/v1/loans", headers=auth("employee"), json={
            "kind": "loan", "principal": principal, "reason": "Family medical costs",
            "instalment_count": count}).json()["id"]
        c.put(f"/api/v1/loans/{loan_id}/decide", headers=auth("admin"),
              json={"approved": True})
        return loan_id

    def test_disbursement_lays_down_the_schedule(self, env):
        c, auth = env["client"], env["auth"]
        loan_id = self._approved_loan(env)
        r = c.post(f"/api/v1/loans/{loan_id}/disburse", headers=auth("admin"),
                   json={"disbursed_on": "2026-09-10"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "disbursed"
        assert body["first_recovery_month"] == "2026-10"
        assert [i["due_month"] for i in body["instalments"]] == \
            ["2026-10", "2026-11", "2026-12"]
        assert body["outstanding"] == 60_000

    def test_nothing_is_owed_before_disbursement(self, env):
        c, auth, ids = env["client"], env["auth"], env["ids"]
        self._approved_loan(env)
        # Approved is not paid. An advance that never left the bank must not
        # start deducting from payslips.
        r = c.get(f"/api/v1/loans/outstanding/{ids['employee']}", headers=auth("admin"))
        assert r.json()["outstanding"] == 0.0

    def test_you_cannot_approve_your_own_advance(self, env):
        c, auth = env["client"], env["auth"]
        loan_id = c.post("/api/v1/loans", headers=auth("admin"), json={
            "principal": 50_000, "reason": "Anything at all"}).json()["id"]
        r = c.put(f"/api/v1/loans/{loan_id}/decide", headers=auth("admin"),
                  json={"approved": True})
        assert r.status_code == 403

    def test_an_employee_cannot_raise_one_for_somebody_else(self, env):
        c, auth, ids = env["client"], env["auth"], env["ids"]
        r = c.post("/api/v1/loans", headers=auth("employee"), json={
            "principal": 10_000, "reason": "Not my money",
            "employee_id": ids["manager"]})
        assert r.status_code == 403

    def test_waiving_an_instalment_reduces_what_is_owed(self, env):
        c, auth = env["client"], env["auth"]
        loan_id = self._approved_loan(env)
        body = c.post(f"/api/v1/loans/{loan_id}/disburse", headers=auth("admin"),
                      json={"disbursed_on": "2026-09-10"}).json()
        inst = body["instalments"][0]["id"]
        r = c.put(f"/api/v1/loans/{loan_id}/instalments/{inst}/waive",
                  headers=auth("admin"), json={"reason": "Hardship"})
        assert r.status_code == 200
        assert r.json()["outstanding"] == 40_000


class TestProbation:
    def test_confirming_issues_a_letter_and_ends_probation(self, env):
        c, auth, ids = env["client"], env["auth"], env["ids"]
        before = c.get(f"/api/v1/probation/{ids['employee']}",
                       headers=auth("manager")).json()
        assert before["status"] == "in_probation"
        assert before["can_decide"] is True

        r = c.post(f"/api/v1/probation/{ids['employee']}/decide",
                   headers=auth("manager"),
                   json={"decision": "confirm", "comments": "Strong first quarter."})
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "confirmed"
        assert r.json()["reviews"][0]["document_id"] is not None

        docs = c.get("/api/v1/documents/me", headers=auth("employee")).json()
        assert any(d["kind"] == "confirmation" for d in docs)

    def test_extending_moves_the_date_and_needs_one(self, env):
        c, auth, ids = env["client"], env["auth"], env["ids"]
        r = c.post(f"/api/v1/probation/{ids['employee']}/decide",
                   headers=auth("manager"), json={"decision": "extend"})
        assert r.status_code == 400
        assert "new end date" in r.json()["detail"]

        future = (date.today() + timedelta(days=90)).isoformat()
        r = c.post(f"/api/v1/probation/{ids['employee']}/decide",
                   headers=auth("manager"),
                   json={"decision": "extend", "new_end_date": future})
        assert r.status_code == 200
        assert r.json()["status"] == "extended"
        assert r.json()["probation_end_date"] == future

    def test_nobody_decides_their_own(self, env):
        c, auth, ids = env["client"], env["auth"], env["ids"]
        r = c.post(f"/api/v1/probation/{ids['admin']}/decide", headers=auth("admin"),
                   json={"decision": "confirm"})
        assert r.status_code == 403

    def test_an_unrelated_colleague_cannot_decide(self, env):
        c, auth, ids = env["client"], env["auth"], env["ids"]
        r = c.post(f"/api/v1/probation/{ids['manager']}/decide",
                   headers=auth("employee"), json={"decision": "confirm"})
        assert r.status_code == 403

    def test_reminders_skip_the_historical_backlog(self, env):
        """
        Switching this on finds everyone who was never formally confirmed,
        including people hired years ago. Those belong in the queue, not in
        fifteen managers' inboxes.
        """
        from app.utils.probation import due_for_review, send_probation_reminders
        db = env["db"]
        # Three of the three are "due": two hired years ago, one last month.
        assert len(due_for_review(db, within_days=3650)) == 3
        result = send_probation_reminders(db)
        assert result["backlog_not_notified"] == 2
        assert result["notified"] == 0     # the new joiner is not due yet

    def test_bulk_confirm_clears_the_backlog_but_spares_the_new_joiner(self, env):
        c, auth, ids = env["client"], env["auth"], env["ids"]
        r = c.post("/api/v1/probation/bulk-confirm", headers=auth("admin"), json={})
        assert r.status_code == 200, r.text
        confirmed = {p["employee_id"] for p in r.json()}
        # The two long-serving people are cleared; the new joiner is not.
        assert ids["admin"] in confirmed and ids["manager"] in confirmed
        assert ids["employee"] not in confirmed


class TestShifts:
    def test_assigning_a_shift_closes_the_previous_one(self, env):
        c, auth, ids = env["client"], env["auth"], env["ids"]
        full = c.post("/api/v1/shifts", headers=auth("admin"), json={
            "name": "General", "start_time": "09:00:00", "end_time": "18:00:00",
            "break_minutes": 60}).json()
        assert full["hours"] == 8.0
        half = c.post("/api/v1/shifts", headers=auth("admin"), json={
            "name": "Morning half", "start_time": "09:00:00", "end_time": "13:00:00"
        }).json()
        assert half["hours"] == 4.0

        c.post("/api/v1/shifts/assignments", headers=auth("admin"), json={
            "employee_id": ids["employee"], "shift_id": full["id"],
            "effective_from": "2026-01-01"})
        c.post("/api/v1/shifts/assignments", headers=auth("admin"), json={
            "employee_id": ids["employee"], "shift_id": half["id"],
            "effective_from": "2026-09-01"})

        r = c.get("/api/v1/shifts/me", headers=auth("employee"))
        assert r.json()["name"] == "Morning half"
        # And the earlier pattern still answers for a date it covered, so last
        # month's attendance is judged against last month's shift.
        r = c.get(f"/api/v1/shifts/employee/{ids['employee']}?on=2026-03-15",
                  headers=auth("admin"))
        assert r.json()["name"] == "General"

    def test_the_attendance_report_judges_punctuality_against_the_shift(self, env):
        from datetime import datetime as dt
        from app.models.attendance import Attendance
        c, auth, ids, db = env["client"], env["auth"], env["ids"], env["db"]

        shift = c.post("/api/v1/shifts", headers=auth("admin"), json={
            "name": "General", "start_time": "09:00:00", "end_time": "18:00:00",
            "break_minutes": 60, "grace_minutes": 15}).json()
        c.post("/api/v1/shifts/assignments", headers=auth("admin"), json={
            "employee_id": ids["employee"], "shift_id": shift["id"],
            "effective_from": "2026-01-01"})

        # 10:05 local on a Wednesday, stored in UTC (PKT is UTC+5).
        day = date(2026, 9, 9)
        db.add(Attendance(employee_id=ids["employee"],
                          check_in_time=dt(2026, 9, 9, 5, 5),
                          check_out_time=dt(2026, 9, 9, 13, 0),
                          duration_hours=7.9))
        db.commit()

        r = c.get("/api/v1/attendance/daily/report", headers=auth("employee"),
                  params={"start_date": "2026-09-01T00:00:00",
                          "end_date": "2026-09-30T23:59:59"})
        assert r.status_code == 200, r.text
        rec = next(x for x in r.json()["daily_records"] if x["date"] == day.isoformat())
        # 09:00 start plus 15 minutes' grace, arrived 10:05 — fifty minutes late.
        assert rec["shift"]["is_late"] is True
        assert rec["shift"]["late_minutes"] == 50
        # Left at 18:00 local, exactly on time.
        assert rec["shift"]["is_early_departure"] is False

    def test_no_shift_means_no_punctuality_verdict(self, env):
        """A company that has never defined when the day starts gets no
        opinion about whether anyone was late."""
        from datetime import datetime as dt
        from app.models.attendance import Attendance
        c, auth, ids, db = env["client"], env["auth"], env["ids"], env["db"]
        db.add(Attendance(employee_id=ids["employee"],
                          check_in_time=dt(2026, 9, 9, 8, 0),
                          check_out_time=dt(2026, 9, 9, 16, 0), duration_hours=8.0))
        db.commit()
        r = c.get("/api/v1/attendance/daily/report", headers=auth("employee"),
                  params={"start_date": "2026-09-01T00:00:00",
                          "end_date": "2026-09-30T23:59:59"})
        assert all("shift" not in rec for rec in r.json()["daily_records"])

    def test_only_one_shift_is_the_default(self, env):
        c, auth = env["client"], env["auth"]
        a = c.post("/api/v1/shifts", headers=auth("admin"), json={
            "name": "A", "start_time": "09:00:00", "end_time": "17:00:00",
            "is_default": True}).json()
        c.post("/api/v1/shifts", headers=auth("admin"), json={
            "name": "B", "start_time": "10:00:00", "end_time": "18:00:00",
            "is_default": True})
        shifts = {s["name"]: s["is_default"] for s in
                  c.get("/api/v1/shifts", headers=auth("admin")).json()}
        assert shifts == {"A": False, "B": True}


class TestPerformance:
    def _open_cycle(self, env):
        c, auth = env["client"], env["auth"]
        cycle = c.post("/api/v1/performance/cycles", headers=auth("admin"), json={
            "name": "H2 2026", "period_start": "2026-07-01",
            "period_end": "2026-12-31"}).json()
        c.post(f"/api/v1/performance/cycles/{cycle['id']}/open",
               headers=auth("admin"), params={"include_probationers": True})
        return cycle["id"]

    def _review_for(self, env, cycle_id, who="employee"):
        c, auth = env["client"], env["auth"]
        rows = c.get("/api/v1/performance/reviews", headers=auth(who),
                     params={"cycle_id": cycle_id, "scope": "mine"}).json()
        return rows[0]["id"]

    def test_the_manager_cannot_write_before_the_self_assessment(self, env):
        c, auth = env["client"], env["auth"]
        cycle_id = self._open_cycle(env)
        review_id = self._review_for(env, cycle_id)
        r = c.put(f"/api/v1/performance/reviews/{review_id}/manager",
                  headers=auth("manager"), json={"manager_assessment": "Good"})
        assert r.status_code == 400
        assert "self-assessment" in r.json()["detail"]

    def test_the_employee_sees_nothing_until_it_is_shared(self, env):
        c, auth = env["client"], env["auth"]
        cycle_id = self._open_cycle(env)
        review_id = self._review_for(env, cycle_id)

        c.put(f"/api/v1/performance/reviews/{review_id}/self", headers=auth("employee"),
              json={"self_assessment": "I shipped the thing.", "self_rating": 4})
        c.put(f"/api/v1/performance/reviews/{review_id}/manager", headers=auth("manager"),
              json={"manager_assessment": "Met expectations.", "overall_rating": 3,
                    "calibration_note": "Do not show this to Eve."})

        seen = c.get(f"/api/v1/performance/reviews/{review_id}",
                     headers=auth("employee")).json()
        assert seen["manager_assessment"] is None
        assert seen["overall_rating"] is None
        assert seen["calibration_note"] is None

        c.post(f"/api/v1/performance/reviews/{review_id}/share", headers=auth("manager"))
        seen = c.get(f"/api/v1/performance/reviews/{review_id}",
                     headers=auth("employee")).json()
        assert seen["manager_assessment"] == "Met expectations."
        assert seen["overall_rating"] == 3
        # Calibration notes stay with the people running the cycle.
        assert seen["calibration_note"] is None

    def test_acknowledging_closes_the_review(self, env):
        c, auth = env["client"], env["auth"]
        cycle_id = self._open_cycle(env)
        review_id = self._review_for(env, cycle_id)
        c.put(f"/api/v1/performance/reviews/{review_id}/self", headers=auth("employee"),
              json={"self_assessment": "A year of work."})
        c.put(f"/api/v1/performance/reviews/{review_id}/manager", headers=auth("manager"),
              json={"manager_assessment": "Agreed.", "overall_rating": 4})
        c.post(f"/api/v1/performance/reviews/{review_id}/share", headers=auth("manager"))
        r = c.post(f"/api/v1/performance/reviews/{review_id}/acknowledge",
                   headers=auth("employee"), json={"employee_comment": "Thank you."})
        assert r.status_code == 200
        assert r.json()["status"] == "closed"

    def test_an_employee_cannot_rate_their_own_goal(self, env):
        c, auth, ids = env["client"], env["auth"], env["ids"]
        goal = c.post("/api/v1/performance/goals", headers=auth("manager"), json={
            "employee_id": ids["employee"], "title": "Cut build time by half",
            "weight": 40}).json()
        r = c.put(f"/api/v1/performance/goals/{goal['id']}", headers=auth("employee"),
                  json={"rating": 5, "employee_comment": "Went well"})
        assert r.status_code == 200
        # The comment lands, the rating does not.
        assert r.json()["rating"] is None
        assert r.json()["employee_comment"] == "Went well"

    def test_goals_seed_from_the_job_profile(self, env):
        c, auth, ids, db = env["client"], env["auth"], env["ids"], env["db"]
        from app.models.document import JobProfile
        db.add(JobProfile(designation="Engineer", department="Engineering",
                          responsibilities=["Build things"],
                          kpis=["Ship two releases a quarter",
                                "Keep p95 latency under 300ms"]))
        db.commit()
        r = c.post("/api/v1/performance/goals/seed-from-profile", headers=auth("manager"),
                   params={"employee_id": ids["employee"]})
        assert r.status_code == 201, r.text
        titles = sorted(g["title"] for g in r.json())
        assert titles == ["Keep p95 latency under 300ms",
                          "Ship two releases a quarter"]
        assert all(g["source"] == "job_profile" for g in r.json())
        # Running it twice adds nothing.
        again = c.post("/api/v1/performance/goals/seed-from-profile",
                       headers=auth("manager"), params={"employee_id": ids["employee"]})
        assert again.json() == []


class TestExitSettlement:
    def _resignation_at_clearance(self, env):
        """Walk a resignation through to CLEARED, the state a settlement needs."""
        c, auth, ids = env["client"], env["auth"], env["ids"]
        # A full notice period. Leaving sooner is legitimate but produces a
        # notice-shortfall deduction large enough to make the settlement
        # negative, which has its own guard and its own test below.
        last_day = (date.today() + timedelta(days=30)).isoformat()
        r = c.post("/api/v1/offboarding/resignations", headers=auth("employee"),
                   json={"reason": "Moving to another city",
                         "proposed_last_working_day": last_day})
        assert r.status_code == 201, r.text
        res_id = r.json()["id"]
        c.put(f"/api/v1/offboarding/resignations/{res_id}/decide",
              headers=auth("manager"), json={"approved": True})
        c.put(f"/api/v1/offboarding/resignations/{res_id}/decide",
              headers=auth("admin"), json={"approved": True})
        detail = c.get("/api/v1/offboarding/resignations",
                       headers=auth("admin")).json()[0]
        for item in detail["clearance_items"]:
            c.put(f"/api/v1/offboarding/clearance/{item['id']}", headers=auth("admin"),
                  json={"status": "completed"})
        c.put(f"/api/v1/offboarding/resignations/{res_id}/decide",
              headers=auth("admin"), json={"approved": True})
        return res_id

    def test_an_exit_cannot_complete_without_an_approved_settlement(self, env):
        c, auth = env["client"], env["auth"]
        res_id = self._resignation_at_clearance(env)
        r = c.post(f"/api/v1/offboarding/resignations/{res_id}/complete",
                   headers=auth("admin"))
        assert r.status_code == 400
        assert "settlement" in r.json()["detail"].lower()

    def test_draft_then_approve_then_complete(self, env):
        c, auth = env["client"], env["auth"]
        res_id = self._resignation_at_clearance(env)

        r = c.post(f"/api/v1/settlements/resignation/{res_id}", headers=auth("admin"))
        assert r.status_code == 201, r.text
        s = r.json()
        assert s["status"] == "draft"
        assert s["monthly_gross"] == 80_000
        assert s["net_payable"] == round(s["gross_payable"] - s["total_deductions"], 2)

        settlement_id = s["id"]
        # A draft is not the employee's business until somebody agrees it.
        assert c.get(f"/api/v1/settlements/{settlement_id}",
                     headers=auth("employee")).status_code == 403

        r = c.post(f"/api/v1/settlements/{settlement_id}/approve", headers=auth("admin"))
        assert r.status_code == 200, r.text
        assert c.get("/api/v1/settlements/me", headers=auth("employee")).json()["id"] \
            == settlement_id

        r = c.post(f"/api/v1/offboarding/resignations/{res_id}/complete",
                   headers=auth("admin"))
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "completed"

    def test_a_hand_edited_line_survives_a_recompute(self, env):
        c, auth = env["client"], env["auth"]
        res_id = self._resignation_at_clearance(env)
        s = c.post(f"/api/v1/settlements/resignation/{res_id}",
                   headers=auth("admin")).json()

        c.put(f"/api/v1/settlements/{s['id']}", headers=auth("admin"),
              json={"gratuity_amount": 25_000})
        again = c.post(f"/api/v1/settlements/resignation/{res_id}",
                       headers=auth("admin")).json()
        assert again["gratuity_amount"] == 25_000
        assert "gratuity_amount" in again["manual_fields"]

    def test_a_negative_settlement_needs_an_explanation(self, env):
        """
        Someone leaving without serving notice can owe the company more than
        their final month pays. That is allowed, but not silently.
        """
        c, auth = env["client"], env["auth"]
        res_id = c.post("/api/v1/offboarding/resignations", headers=auth("employee"),
                        json={"reason": "Leaving immediately for family reasons",
                              "proposed_last_working_day": date.today().isoformat()}
                        ).json()["id"]
        c.put(f"/api/v1/offboarding/resignations/{res_id}/decide",
              headers=auth("manager"), json={"approved": True})
        c.put(f"/api/v1/offboarding/resignations/{res_id}/decide",
              headers=auth("admin"), json={"approved": True})

        s = c.post(f"/api/v1/settlements/resignation/{res_id}",
                   headers=auth("admin")).json()
        # Thirty days of notice not served, at a calendar-day rate.
        assert s["notice_shortfall_days"] == 30
        assert s["notice_shortfall_amount"] == 80_000
        assert s["net_payable"] < 0

        r = c.post(f"/api/v1/settlements/{s['id']}/approve", headers=auth("admin"))
        assert r.status_code == 400
        assert "negative" in r.json()["detail"].lower()

        c.put(f"/api/v1/settlements/{s['id']}", headers=auth("admin"),
              json={"notes": "Balance to be recovered by bank transfer."})
        r = c.post(f"/api/v1/settlements/{s['id']}/approve", headers=auth("admin"))
        assert r.status_code == 200

    def test_an_approved_settlement_is_not_editable(self, env):
        c, auth = env["client"], env["auth"]
        res_id = self._resignation_at_clearance(env)
        s = c.post(f"/api/v1/settlements/resignation/{res_id}",
                   headers=auth("admin")).json()
        c.post(f"/api/v1/settlements/{s['id']}/approve", headers=auth("admin"))
        r = c.put(f"/api/v1/settlements/{s['id']}", headers=auth("admin"),
                  json={"other_deduction": 1000})
        assert r.status_code == 400

    def test_outstanding_equipment_blocks_clearance(self, env):
        c, auth, ids = env["client"], env["auth"], env["ids"]
        asset_id = c.post("/api/v1/assets", headers=auth("admin"), json={
            "tag": "QG-LT-900", "category": "laptop", "name": "ThinkPad"}).json()["id"]
        c.post(f"/api/v1/assets/{asset_id}/issue", headers=auth("admin"),
               json={"employee_id": ids["employee"]})

        last_day = (date.today() + timedelta(days=30)).isoformat()
        res_id = c.post("/api/v1/offboarding/resignations", headers=auth("employee"),
                        json={"reason": "Moving on to something new",
                              "proposed_last_working_day": last_day}).json()["id"]
        c.put(f"/api/v1/offboarding/resignations/{res_id}/decide",
              headers=auth("manager"), json={"approved": True})
        c.put(f"/api/v1/offboarding/resignations/{res_id}/decide",
              headers=auth("admin"), json={"approved": True})

        detail = c.get("/api/v1/offboarding/resignations", headers=auth("admin")).json()[0]
        # The register put a named line on the checklist, not a generic one.
        labels = [i["label"] for i in detail["clearance_items"]]
        assert any("ThinkPad (QG-LT-900)" in l for l in labels)

        for item in detail["clearance_items"]:
            c.put(f"/api/v1/offboarding/clearance/{item['id']}", headers=auth("admin"),
                  json={"status": "completed"})
        # Every box ticked, but the laptop is still on the register.
        r = c.put(f"/api/v1/offboarding/resignations/{res_id}/decide",
                  headers=auth("admin"), json={"approved": True})
        assert r.status_code == 400
        assert "still issued" in r.json()["detail"]

        c.post(f"/api/v1/assets/{asset_id}/return", headers=auth("admin"),
               json={"condition_in": "good"})
        r = c.put(f"/api/v1/offboarding/resignations/{res_id}/decide",
                  headers=auth("admin"), json={"approved": True})
        assert r.status_code == 200
        assert r.json()["status"] == "cleared"


class TestOrgChartAndJobs:
    def test_the_chart_nests_reports_under_their_manager(self, env):
        c, auth, ids = env["client"], env["auth"], env["ids"]
        r = c.get("/api/v1/users/org-chart", headers=auth("employee"))
        assert r.status_code == 200, r.text
        roots = {n["id"]: n for n in r.json()}
        # Nobody manages the admin or the manager, so both are roots.
        assert set(roots) == {ids["admin"], ids["manager"]}
        assert [n["id"] for n in roots[ids["manager"]]["reports"]] == [ids["employee"]]

    def test_the_jobs_panel_lists_every_sweep(self, env):
        c, auth = env["client"], env["auth"]
        r = c.get("/api/v1/admin/jobs", headers=auth("admin"))
        assert r.status_code == 200, r.text
        names = {j["name"] for j in r.json()}
        assert names == {"leave_reminders", "complete_due_exits", "biometric_sync",
                         "inventory_digest",
                         "probation_reminders", "profile_reminders", "leave_accrual"}
        # Never run on this database, so everything is due and nothing running.
        assert all(j["is_due"] and not j["is_running"] for j in r.json())

    def test_the_jobs_panel_is_not_for_employees(self, env):
        c, auth = env["client"], env["auth"]
        assert c.get("/api/v1/admin/jobs", headers=auth("employee")).status_code == 403


class TestBulkMarkPaid:
    """
    Settling payroll asserts that money moved. Both guards exist because the
    unguarded version already put a September payment date on four July
    payslips.
    """

    def _payslip(self, env, month, year=2026, net=80_000.0):
        from app.models.payroll import Payroll, PayrollStatusEnum
        db = env["db"]
        p = Payroll(employee_id=env["ids"]["employee"], month=month, year=year,
                    base_salary=net, net_salary=net,
                    status=PayrollStatusEnum.PROCESSED)
        db.add(p)
        db.commit()
        return p.id

    def test_it_refuses_to_guess_the_payment_date(self, env):
        c, auth = env["client"], env["auth"]
        self._payslip(env, 7)
        r = c.post("/api/v1/payroll/mark-paid", headers=auth("admin"),
                   json={"month": 7, "year": 2026})
        assert r.status_code == 400
        assert "date the payment was made" in r.json()["detail"]

    def test_an_unknown_date_is_left_blank_not_filled_with_today(self, env):
        from app.models.payroll import Payroll
        c, auth, db = env["client"], env["auth"], env["db"]
        pid = self._payslip(env, 7)
        r = c.post("/api/v1/payroll/mark-paid", headers=auth("admin"),
                   json={"month": 7, "year": 2026, "payment_date_unknown": True})
        assert r.status_code == 200, r.text
        assert r.json()["marked_paid"] == 1
        assert r.json()["payment_date_recorded"] is False
        db.expire_all()
        assert db.query(Payroll).get(pid).payment_date is None

    def test_a_stated_date_is_recorded(self, env):
        from app.models.payroll import Payroll
        c, auth, db = env["client"], env["auth"], env["db"]
        pid = self._payslip(env, 8)
        r = c.post("/api/v1/payroll/mark-paid", headers=auth("admin"),
                   json={"month": 8, "year": 2026,
                         "payment_date": "2026-08-31T00:00:00",
                         "payment_method": "Bank transfer",
                         "payment_reference": "BATCH-0831"})
        assert r.status_code == 200, r.text
        db.expire_all()
        row = db.query(Payroll).get(pid)
        assert row.payment_date.date().isoformat() == "2026-08-31"
        assert row.payment_reference == "BATCH-0831"

    def test_a_wrong_expected_count_settles_nothing(self, env):
        from app.models.payroll import Payroll, PayrollStatusEnum
        c, auth, db = env["client"], env["auth"], env["db"]
        pid = self._payslip(env, 9)
        r = c.post("/api/v1/payroll/mark-paid", headers=auth("admin"),
                   json={"month": 9, "year": 2026, "payment_date_unknown": True,
                         "expected_count": 5})
        assert r.status_code == 409
        assert "not the 5 you expected" in r.json()["detail"]
        db.expire_all()
        assert db.query(Payroll).get(pid).status == PayrollStatusEnum.PROCESSED

    def test_it_will_not_settle_everything_by_default(self, env):
        c, auth = env["client"], env["auth"]
        self._payslip(env, 10)
        r = c.post("/api/v1/payroll/mark-paid", headers=auth("admin"), json={})
        assert r.status_code == 400
        assert "which payslips" in r.json()["detail"]

    def test_repeating_it_changes_nothing(self, env):
        c, auth = env["client"], env["auth"]
        self._payslip(env, 11)
        body = {"month": 11, "year": 2026, "payment_date_unknown": True}
        assert c.post("/api/v1/payroll/mark-paid", headers=auth("admin"),
                      json=body).json()["marked_paid"] == 1
        assert c.post("/api/v1/payroll/mark-paid", headers=auth("admin"),
                      json=body).json()["marked_paid"] == 0

    def test_an_employee_cannot_settle_payroll(self, env):
        c, auth = env["client"], env["auth"]
        self._payslip(env, 12)
        r = c.post("/api/v1/payroll/mark-paid", headers=auth("employee"),
                   json={"month": 12, "year": 2026, "payment_date_unknown": True})
        assert r.status_code == 403


def _seed_templates(db):
    """The four role forms, as the seed script would create them."""
    from app.models.performance import ReviewTemplate
    from app.utils.review_templates import DEFAULT_TEMPLATE, SECTIONS, TEMPLATES
    for name, content in TEMPLATES.items():
        db.add(ReviewTemplate(
            name=name, description=content["description"],
            designations=list(content["designations"]),
            competencies=[dict(c) for c in content["competencies"]],
            sections=[dict(s) for s in SECTIONS],
            goal_weight=content["goal_weight"],
            competency_weight=content["competency_weight"],
            is_default=(name == DEFAULT_TEMPLATE), is_active=True))
    db.commit()


class TestReviewForm:
    """
    The structured review: who fills what, in which order, and what the number
    at the end is made of.
    """

    def _cycle_and_review(self, env, seed_goals=True):
        c, auth, ids, db = env["client"], env["auth"], env["ids"], env["db"]
        _seed_templates(db)
        cycle = c.post("/api/v1/performance/cycles", headers=auth("admin"), json={
            "name": "H2 2026", "period_start": "2026-07-01",
            "period_end": "2026-12-31"}).json()
        c.post(f"/api/v1/performance/cycles/{cycle['id']}/open", headers=auth("admin"),
               params={"include_probationers": True})
        review = c.get("/api/v1/performance/reviews", headers=auth("employee"),
                       params={"cycle_id": cycle["id"], "scope": "mine"}).json()[0]
        if seed_goals:
            for title, weight in [("Ship the v2 board", 60), ("Cut build time", 40)]:
                c.post("/api/v1/performance/goals", headers=auth("manager"), json={
                    "employee_id": ids["employee"], "cycle_id": cycle["id"],
                    "title": title, "weight": weight})
        return cycle["id"], review["id"]

    def test_the_form_says_who_fills_each_field(self, env):
        c, auth = env["client"], env["auth"]
        _, review_id = self._cycle_and_review(env)

        form = c.get(f"/api/v1/performance/reviews/{review_id}/form",
                     headers=auth("employee")).json()
        # Eve is an Engineer, so she gets the individual-contributor form.
        assert form["template_name"] == "Engineering — Individual Contributor"
        assert form["your_part"] == "employee"
        assert form["you_can_edit"] is True
        assert form["waiting_on"] == "the employee's self-assessment"

        keys = {c_["key"] for c_ in form["competencies"]}
        assert "technical_quality" in keys and "documentation" in keys
        # Every competency is rated by both sides, separately.
        assert all(c_["rated_by"] == "both" for c_ in form["competencies"])
        assert all(c_["self"]["editable"] for c_ in form["competencies"])
        assert not any(c_["manager"]["editable"] for c_ in form["competencies"])

        employee_sections = {s["key"] for s in form["sections"]
                             if s["rated_by"] == "employee"}
        manager_sections = {s["key"] for s in form["sections"]
                            if s["rated_by"] == "manager"}
        assert employee_sections == {"achievements", "challenges",
                                     "support_needed", "career_aspiration"}
        assert manager_sections == {"impact_summary", "growth_plan"}
        # The scale is served with the form so both sides read the same words.
        assert [s["value"] for s in form["rating_scale"]] == [1, 2, 3, 4, 5]

    def test_the_manager_sees_their_own_half_as_editable(self, env):
        c, auth = env["client"], env["auth"]
        _, review_id = self._cycle_and_review(env)
        c.put(f"/api/v1/performance/reviews/{review_id}/self", headers=auth("employee"),
              json={"sections": {"achievements": "Shipped the v2 board."},
                    "scores": {"technical_quality": {"rating": 4}}})
        form = c.get(f"/api/v1/performance/reviews/{review_id}/form",
                     headers=auth("manager")).json()
        assert form["your_part"] == "manager"
        assert all(c_["manager"]["editable"] for c_ in form["competencies"])
        assert not any(c_["self"]["editable"] for c_ in form["competencies"])
        # And the manager can read what the employee wrote — that is the point.
        tq = next(c_ for c_ in form["competencies"] if c_["key"] == "technical_quality")
        assert tq["self"]["rating"] == 4

    def test_a_required_section_blocks_submission(self, env):
        c, auth = env["client"], env["auth"]
        _, review_id = self._cycle_and_review(env)
        r = c.put(f"/api/v1/performance/reviews/{review_id}/self",
                  headers=auth("employee"),
                  json={"scores": {"technical_quality": {"rating": 4}}, "submit": True})
        assert r.status_code == 400
        assert "What you delivered" in r.json()["detail"]

    def test_an_unknown_competency_is_refused(self, env):
        c, auth = env["client"], env["auth"]
        _, review_id = self._cycle_and_review(env)
        r = c.put(f"/api/v1/performance/reviews/{review_id}/self",
                  headers=auth("employee"),
                  json={"scores": {"charisma": {"rating": 5}}, "submit": False})
        assert r.status_code == 400
        assert "not a competency" in r.json()["detail"]

    def test_a_rating_outside_the_scale_is_refused(self, env):
        c, auth = env["client"], env["auth"]
        _, review_id = self._cycle_and_review(env)
        r = c.put(f"/api/v1/performance/reviews/{review_id}/self",
                  headers=auth("employee"),
                  json={"scores": {"technical_quality": {"rating": 47}},
                        "submit": False})
        assert r.status_code == 400
        assert "1 to 5" in r.json()["detail"]

    def test_the_score_is_goals_and_competencies_at_the_stated_weights(self, env):
        c, auth, db = env["client"], env["auth"], env["db"]
        cycle_id, review_id = self._cycle_and_review(env)

        c.put(f"/api/v1/performance/reviews/{review_id}/self", headers=auth("employee"),
              json={"sections": {"achievements": "Shipped the v2 board."},
                    "scores": {k: {"rating": 5} for k in
                               ("technical_quality", "delivery", "problem_solving",
                                "collaboration", "ownership", "documentation")},
                    "submit": True})

        goals = c.get("/api/v1/performance/goals", headers=auth("manager"),
                      params={"employee_id": env["ids"]["employee"],
                              "cycle_id": cycle_id}).json()
        by_title = {g["title"]: g["id"] for g in goals}

        r = c.put(f"/api/v1/performance/reviews/{review_id}/manager",
                  headers=auth("manager"),
                  json={"sections": {"impact_summary": "Carried the board release.",
                                     "growth_plan": "Write more down."},
                        # Every competency 4 -> competency score 4.0
                        "scores": {k: {"rating": 4} for k in
                                   ("technical_quality", "delivery", "problem_solving",
                                    "collaboration", "ownership", "documentation")},
                        # 60% weight at 5, 40% weight at 2.5 -> goal score 4.0
                        "goal_ratings": {
                            str(by_title["Ship the v2 board"]): {"rating": 5},
                            str(by_title["Cut build time"]): {"rating": 2.5}},
                        "submit": True})
        assert r.status_code == 200, r.text

        score = c.get(f"/api/v1/performance/reviews/{review_id}/score",
                      headers=auth("manager")).json()
        assert score["competency_score"] == 4.0
        assert score["goal_score"] == 4.0
        assert score["final_score"] == 4.0
        assert score["band"] == "Exceeds"
        # The employee rated themselves a whole point higher.
        assert score["self_assessment_gap"] == 1.0
        assert score["goal_weight"] == 60.0 and score["competency_weight"] == 40.0

    def test_the_employee_cannot_see_their_score_before_it_is_shared(self, env):
        c, auth = env["client"], env["auth"]
        cycle_id, review_id = self._cycle_and_review(env)
        c.put(f"/api/v1/performance/reviews/{review_id}/self", headers=auth("employee"),
              json={"sections": {"achievements": "A year of work."}, "submit": True})
        c.put(f"/api/v1/performance/reviews/{review_id}/manager", headers=auth("manager"),
              json={"sections": {"impact_summary": "Solid.", "growth_plan": "More docs."},
                    "scores": {"technical_quality": {"rating": 3}}, "submit": True})

        r = c.get(f"/api/v1/performance/reviews/{review_id}/score", headers=auth("employee"))
        assert r.status_code == 403

        form = c.get(f"/api/v1/performance/reviews/{review_id}/form",
                     headers=auth("employee")).json()
        tq = next(c_ for c_ in form["competencies"] if c_["key"] == "technical_quality")
        assert tq["manager"]["rating"] is None

        c.post(f"/api/v1/performance/reviews/{review_id}/share", headers=auth("manager"))
        assert c.get(f"/api/v1/performance/reviews/{review_id}/score",
                     headers=auth("employee")).status_code == 200
        form = c.get(f"/api/v1/performance/reviews/{review_id}/form",
                     headers=auth("employee")).json()
        tq = next(c_ for c_ in form["competencies"] if c_["key"] == "technical_quality")
        assert tq["manager"]["rating"] == 3

    def test_the_leaderboard_ranks_the_scored_and_still_lists_the_rest(self, env):
        c, auth, db = env["client"], env["auth"], env["db"]
        cycle_id, review_id = self._cycle_and_review(env, seed_goals=False)
        c.put(f"/api/v1/performance/reviews/{review_id}/self", headers=auth("employee"),
              json={"sections": {"achievements": "Work."}, "submit": True})
        c.put(f"/api/v1/performance/reviews/{review_id}/manager", headers=auth("manager"),
              json={"sections": {"impact_summary": "Good.", "growth_plan": "More."},
                    "scores": {"technical_quality": {"rating": 5}}, "submit": True})

        rows = c.get("/api/v1/performance/leaderboard", headers=auth("admin"),
                     params={"cycle_id": cycle_id}).json()
        scored = [r for r in rows if r["final_score"] is not None]
        assert scored[0]["rank"] == 1
        assert scored[0]["employee_name"] == "Eve Employee"
        # The manager and admin have reviews too, unstarted and unranked.
        assert any(r["rank"] is None for r in rows)
        assert len(rows) == 3

    def test_templates_must_weigh_a_hundred(self, env):
        c, auth = env["client"], env["auth"]
        r = c.post("/api/v1/performance/templates", headers=auth("admin"), json={
            "name": "Broken", "competencies": [
                {"key": "a", "title": "A", "weight": 30},
                {"key": "b", "title": "B", "weight": 30}]})
        assert r.status_code == 400
        assert "add up to 60" in r.json()["detail"]

    def test_the_right_form_reaches_the_right_role(self, env):
        c, auth, db = env["client"], env["auth"], env["db"]
        _seed_templates(db)
        for designation, expected in [
                ("Hardware Design Engineer", "Engineering — Individual Contributor"),
                ("Embedded Systems Engineer", "Engineering — Team Lead"),
                ("Project Manager", "Product & Design"),
                ("Chief Executive Officer (CEO)", "Executive"),
                ("Chief Astrologer", "Engineering — Individual Contributor")]:
            got = c.get(f"/api/v1/performance/templates/for/{designation}",
                        headers=auth("admin")).json()
            assert got["name"] == expected, designation


class TestEmployeeOfTheMonth:

    def _open(self, env, month=None, year=None):
        # This month, not a fixed one. /awards/current filters on today, so a
        # hardcoded September passed while it was September and started
        # failing on 1 October — the award existed, just not "currently".
        today = date.today()
        month = month or today.month
        year = year or today.year
        c, auth = env["client"], env["auth"]
        r = c.post("/api/v1/performance/awards", headers=auth("admin"),
                   json={"month": month, "year": year})
        assert r.status_code == 201, r.text
        return r.json()["id"]

    def test_nominating_a_colleague(self, env):
        c, auth, ids = env["client"], env["auth"], env["ids"]
        award_id = self._open(env)
        r = c.post(f"/api/v1/performance/awards/{award_id}/nominate",
                   headers=auth("manager"),
                   json={"nominee_id": ids["employee"],
                         "reason": "Carried the v2 board release single-handed."})
        assert r.status_code == 200, r.text
        assert r.json()["nomination_count"] == 1
        assert r.json()["you_have_nominated"] is True

    def test_you_cannot_nominate_yourself(self, env):
        c, auth, ids = env["client"], env["auth"], env["ids"]
        award_id = self._open(env)
        r = c.post(f"/api/v1/performance/awards/{award_id}/nominate",
                   headers=auth("employee"),
                   json={"nominee_id": ids["employee"],
                         "reason": "I was really quite good this month."})
        assert r.status_code == 400
        assert "yourself" in r.json()["detail"]

    def test_a_reason_is_required_and_has_to_say_something(self, env):
        c, auth, ids = env["client"], env["auth"], env["ids"]
        award_id = self._open(env)
        r = c.post(f"/api/v1/performance/awards/{award_id}/nominate",
                   headers=auth("manager"),
                   json={"nominee_id": ids["employee"], "reason": "good"})
        assert r.status_code == 422    # below the minimum length

    def test_nominating_twice_edits_rather_than_double_counts(self, env):
        c, auth, ids = env["client"], env["auth"], env["ids"]
        award_id = self._open(env)
        body = {"nominee_id": ids["employee"], "reason": "First reason, long enough."}
        c.post(f"/api/v1/performance/awards/{award_id}/nominate",
               headers=auth("manager"), json=body)
        r = c.post(f"/api/v1/performance/awards/{award_id}/nominate",
                   headers=auth("manager"),
                   json={**body, "reason": "A better reason, also long enough."})
        assert r.json()["nomination_count"] == 1

    def test_the_tally_is_hidden_while_nominations_are_open(self, env):
        c, auth, ids = env["client"], env["auth"], env["ids"]
        award_id = self._open(env)
        c.post(f"/api/v1/performance/awards/{award_id}/nominate", headers=auth("manager"),
               json={"nominee_id": ids["employee"], "reason": "Did excellent work."})
        # An employee sees only what they wrote — a running tally would turn
        # nominating into voting for whoever is ahead.
        seen = c.get("/api/v1/performance/awards/current", headers=auth("employee")).json()
        assert seen["tally"] == []
        assert seen["nominations"] == []
        # Whoever decides sees everything.
        seen = c.get("/api/v1/performance/awards/current", headers=auth("admin")).json()
        assert seen["tally"][0]["employee_name"] == "Eve Employee"
        assert seen["tally"][0]["nominations"] == 1

    def test_a_winner_needs_a_citation(self, env):
        c, auth, ids = env["client"], env["auth"], env["ids"]
        award_id = self._open(env)
        r = c.post(f"/api/v1/performance/awards/{award_id}/decide", headers=auth("admin"),
                   json={"winner_id": ids["employee"]})
        assert r.status_code == 400
        assert "citation" in r.json()["detail"]

    def test_deciding_announces_it(self, env):
        from app.models.announcement import Announcement
        c, auth, ids, db = env["client"], env["auth"], env["ids"], env["db"]
        award_id = self._open(env)
        r = c.post(f"/api/v1/performance/awards/{award_id}/decide", headers=auth("admin"),
                   json={"winner_id": ids["employee"],
                         "citation": "Carried the v2 board release."})
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "decided"
        assert r.json()["winner_name"] == "Eve Employee"
        assert r.json()["announced_at"] is not None
        posts = db.query(Announcement).all()
        assert len(posts) == 1
        # The month the fixture opened, whichever one that is today.
        today = date.today()
        assert f"{calendar.month_name[today.month]} {today.year}" in posts[0].title

    def test_a_month_with_no_winner_is_recorded_as_such(self, env):
        c, auth = env["client"], env["auth"]
        award_id = self._open(env)
        r = c.post(f"/api/v1/performance/awards/{award_id}/decide", headers=auth("admin"),
                   json={"no_award": True, "citation": "Nobody was nominated."})
        assert r.status_code == 200
        assert r.json()["status"] == "decided"
        assert r.json()["winner_id"] is None

    def test_the_same_month_cannot_be_opened_twice(self, env):
        c, auth = env["client"], env["auth"]
        self._open(env)
        today = date.today()
        r = c.post("/api/v1/performance/awards", headers=auth("admin"),
                   json={"month": today.month, "year": today.year})
        assert r.status_code == 400
        assert "already open" in r.json()["detail"]

    def test_signals_put_the_month_on_record_next_to_the_nominations(self, env):
        c, auth, ids = env["client"], env["auth"], env["ids"]
        award_id = self._open(env)
        c.post(f"/api/v1/performance/awards/{award_id}/nominate", headers=auth("manager"),
               json={"nominee_id": ids["employee"], "reason": "Shipped the release."})
        s = c.get(f"/api/v1/performance/awards/{award_id}/signals",
                  headers=auth("admin")).json()
        top = s["candidates"][0]
        assert top["employee_name"] == "Eve Employee"
        assert top["nominations"] == 1
        assert s["working_days"] > 0
        # Lateness is unknowable with no shift defined, and says so.
        assert top["late_days"] is None

    def test_deleting_a_month_takes_its_notifications_with_it(self, env):
        from app.models.notification import Notification
        c, auth, ids, db = env["client"], env["auth"], env["ids"], env["db"]
        award_id = self._open(env)
        c.post(f"/api/v1/performance/awards/{award_id}/nominate", headers=auth("manager"),
               json={"nominee_id": ids["employee"], "reason": "Shipped the release."})
        assert db.query(Notification).filter(
            Notification.title.like("Employee of the Month%")).count() == 3

        r = c.delete(f"/api/v1/performance/awards/{award_id}", headers=auth("admin"))
        assert r.status_code == 200, r.text
        assert r.json()["nominations_removed"] == 1
        assert r.json()["notifications_removed"] == 3
        assert c.get("/api/v1/performance/awards/current",
                     headers=auth("admin")).json() is None
        db.expire_all()
        assert db.query(Notification).filter(
            Notification.title.like("Employee of the Month%")).count() == 0

    def test_the_notifications_can_be_kept(self, env):
        from app.models.notification import Notification
        c, auth, db = env["client"], env["auth"], env["db"]
        award_id = self._open(env)
        r = c.delete(f"/api/v1/performance/awards/{award_id}", headers=auth("admin"),
                     params={"remove_notifications": False})
        assert r.json()["notifications_removed"] == 0
        db.expire_all()
        # Erasing the record and rewriting what people were told are two
        # different decisions, and the caller gets to make them separately.
        assert db.query(Notification).filter(
            Notification.title.like("Employee of the Month%")).count() == 3

    def test_only_hr_can_remove_a_month(self, env):
        c, auth = env["client"], env["auth"]
        award_id = self._open(env)
        assert c.delete(f"/api/v1/performance/awards/{award_id}",
                        headers=auth("employee")).status_code == 403

    def test_signals_are_not_for_employees(self, env):
        c, auth = env["client"], env["auth"]
        award_id = self._open(env)
        assert c.get(f"/api/v1/performance/awards/{award_id}/signals",
                     headers=auth("employee")).status_code == 403


class TestPictureRendering:
    """
    The bug this fixes: every profile picture in the portal came back 401 and
    rendered as a broken image, because an <img> tag cannot send a token.
    """

    def _with_picture(self, env, field="profile_picture_url"):
        c, auth, ids, db = env["client"], env["auth"], env["ids"], env["db"]
        from app.models.user import User
        u = db.query(User).get(ids["employee"])
        setattr(u, field, "https://api.example.com/api/v1/upload/pic123.png")
        db.commit()
        return u

    def test_a_profile_picture_comes_back_signed(self, env):
        c, auth = env["client"], env["auth"]
        self._with_picture(env)
        me = c.get("/api/v1/users/me", headers=auth("employee")).json()
        assert "sig=" in me["profile_picture_url"]
        assert "exp=" in me["profile_picture_url"]

    def test_the_org_chart_signs_its_avatars(self, env):
        c, auth, ids = env["client"], env["auth"], env["ids"]
        self._with_picture(env)
        chart = c.get("/api/v1/users/org-chart", headers=auth("employee")).json()

        def walk(nodes):
            for n in nodes:
                yield n
                yield from walk(n["reports"])

        node = next(n for n in walk(chart) if n["id"] == ids["employee"])
        assert "sig=" in node["profile_picture_url"]

    def test_a_cnic_scan_is_not_auto_signed(self, env):
        """
        The deciding difference. A profile picture is directory information —
        an org chart exists so colleagues can see it. A national ID scan is
        not, and stays behind the ownership check.
        """
        c, auth = env["client"], env["auth"]
        self._with_picture(env, field="cnic_front_url")
        me = c.get("/api/v1/users/me", headers=auth("employee")).json()
        assert "sig=" not in me["cnic_front_url"]

    def test_an_unsigned_request_is_still_refused(self, env):
        c = env["client"]
        # No token, no signature: 404, not a hint that the file exists.
        r = c.get("/api/v1/upload/pic123.png")
        assert r.status_code == 404

    def test_a_forged_signature_is_refused(self, env):
        c = env["client"]
        import time
        exp = int(time.time()) + 600
        r = c.get(f"/api/v1/upload/pic123.png?exp={exp}&sig={'0' * 32}")
        assert r.status_code == 404


class TestAnalytics:
    """
    Charts of an empty month are the recurring bug here: a month nobody has
    been paid for is not a month the payroll was zero, and a month before the
    device sync started is not a month nobody came to work. Both drew a floor
    on the chart that read as a collapse.
    """

    def _payslip(self, env, month, year=2026, net=100_000.0):
        from app.models.payroll import Payroll, PayrollStatusEnum
        db = env["db"]
        db.add(Payroll(employee_id=env["ids"]["employee"], month=month, year=year,
                       base_salary=net, net_salary=net, tax=5_000.0,
                       status=PayrollStatusEnum.PAID))
        db.commit()

    def test_the_payroll_series_stops_at_the_last_month_paid(self, env):
        from datetime import date as _date
        c, auth = env["client"], env["auth"]
        today = _date.today()
        prev = today.month - 1 or 12
        year = today.year if today.month > 1 else today.year - 1
        self._payslip(env, prev, year)

        s = c.get("/api/v1/analytics/payroll", headers=auth("admin"),
                  params={"months": 6}).json()
        assert s["months"][-1] == f"{year:04d}-{prev:02d}"
        assert s["current_month_pending"] is True
        # No trailing zero to draw a cliff with.
        assert s["gross"][-1] > 0

    def test_an_empty_overtime_period_says_so(self, env):
        c, auth = env["client"], env["auth"]
        s = c.get("/api/v1/analytics/overtime", headers=auth("admin")).json()
        assert s["has_data"] is False
        assert s["total_hours"] == 0.0

    def test_months_with_no_attendance_are_gaps_not_zeroes(self, env):
        c, auth = env["client"], env["auth"]
        s = c.get("/api/v1/analytics/attendance", headers=auth("admin"),
                  params={"months": 3}).json()
        assert all(r is None for r in s["attendance_rate_percent"])
        assert s["records_begin"] is None

    def test_leave_liability_is_reported_both_ways(self, env):
        from app.models.leave_balance import LeaveBalance
        from datetime import date as _date
        c, auth, ids, db = env["client"], env["auth"], env["ids"], env["db"]
        db.add(LeaveBalance(employee_id=ids["employee"], year=_date.today().year,
                            annual_total=14.0, annual_used=0.0, annual_carryover=0.0))
        db.commit()

        o = c.get("/api/v1/analytics/overview", headers=auth("admin")).json()
        # Eve joined a month ago, so she cannot have accrued a full year.
        assert o["leave_liability_days"] == 14.0
        assert 0 < o["leave_accrued_days"] < o["leave_liability_days"]

    def test_compensation_counts_part_time_as_paid(self, env):
        c, auth, ids, db = env["client"], env["auth"], env["ids"], env["db"]
        from app.models.user import User
        db.query(User).get(ids["employee"]).standard_hours = 4.0
        db.commit()
        comp = c.get("/api/v1/analytics/compensation", headers=auth("admin")).json()
        # 200,000 + 150,000 + 80,000 — the part-timer at what she is actually
        # paid, not at a full-time equivalent.
        assert comp["monthly_cost"] == 430_000
        assert comp["annual_cost"] == 430_000 * 12
        assert comp["lowest"] == 80_000 and comp["highest"] == 200_000
        assert comp["spread_ratio"] == 2.5

    def test_attention_puts_the_worst_first(self, env):
        c, auth = env["client"], env["auth"]
        items = c.get("/api/v1/analytics/attention", headers=auth("admin")).json()
        keys = [i["key"] for i in items]
        assert "contract_missing" in keys
        severities = [i["severity"] for i in items]
        order = {"critical": 0, "warning": 1, "info": 2}
        assert severities == sorted(severities, key=lambda s: order[s])
        contract = next(i for i in items if i["key"] == "contract_missing")
        assert contract["severity"] == "critical"
        assert "Eve Employee" in contract["people"]

    def test_analytics_is_not_for_employees(self, env):
        c, auth = env["client"], env["auth"]
        for path in ("overview", "compensation", "attention", "attendance"):
            assert c.get(f"/api/v1/analytics/{path}",
                         headers=auth("employee")).status_code == 403


class TestSensitiveEditsAreAudited:
    """
    Changing somebody's bank account redirects their salary. That is the oldest
    payroll fraud there is, and it used to leave no trace whatsoever.
    """

    def test_a_bank_account_change_is_recorded(self, env):
        from app.models.audit_log import AuditLog
        c, auth, ids, db = env["client"], env["auth"], env["ids"], env["db"]
        c.put(f"/api/v1/users/{ids['employee']}", headers=auth("admin"),
              json={"account_number": "PK00AAAA0000000000000001",
                    "account_title": "Eve Employee"})
        row = (db.query(AuditLog).filter(AuditLog.action == "user.updated",
                                         AuditLog.target_id == ids["employee"])
               .order_by(AuditLog.id.desc()).first())
        assert row is not None, "a salary destination changed with no audit row"
        detail = json.loads(row.detail)
        assert "account_number" in detail
        assert detail["account_number"]["to"].endswith("0001")
        # Old and new both recorded — an investigation needs to know what it
        # was changed from as well as to.
        assert detail["account_number"]["from"] is None

    def test_identity_and_pay_drivers_are_recorded(self, env):
        from app.models.audit_log import AuditLog
        c, auth, ids, db = env["client"], env["auth"], env["ids"], env["db"]
        c.put(f"/api/v1/users/{ids['employee']}", headers=auth("admin"),
              json={"cnic_number": "36502-8764580-7", "designation": "Team Lead"})
        row = (db.query(AuditLog).filter(AuditLog.action == "user.updated",
                                         AuditLog.target_id == ids["employee"])
               .order_by(AuditLog.id.desc()).first())
        assert set(json.loads(row.detail)) == {"cnic_number", "designation"}

    def test_a_phone_number_is_not_worth_a_row(self, env):
        from app.models.audit_log import AuditLog
        c, auth, ids, db = env["client"], env["auth"], env["ids"], env["db"]
        before = db.query(AuditLog).filter(AuditLog.action == "user.updated").count()
        c.put(f"/api/v1/users/{ids['employee']}", headers=auth("admin"),
              json={"phone": "03009999999", "address": "Somewhere else"})
        # One row per profile tweak makes the trail unreadable, which is its
        # own kind of failure.
        assert db.query(AuditLog).filter(
            AuditLog.action == "user.updated").count() == before


class TestProfileCompletion:
    """
    The login payload has to carry this, or the frontend cannot ask somebody to
    finish their record at sign-in without a second round trip.
    """

    def _fill(self, env, **kw):
        from app.models.user import User
        db = env["db"]
        u = db.query(User).get(env["ids"]["employee"])
        for k, v in kw.items():
            setattr(u, k, v)
        db.commit()
        return u

    def _complete(self, env):
        from datetime import datetime as _dt
        self._fill(env, phone="03001234567", date_of_birth=_dt(1998, 4, 2),
                   cnic_number="61101-1234567-1", father_name="A Parent",
                   address="Islamabad", emergency_contact_name="A Relative",
                   emergency_contact_phone="03007654321",
                   account_title="Eve Employee", account_number="PK00XXXX",
                   bank_number="Meezan")

    def test_a_new_joiner_is_asked_to_finish(self, env):
        c, auth = env["client"], env["auth"]
        me = c.get("/api/v1/users/me", headers=auth("employee")).json()
        pc = me["profile_completion"]
        assert pc["is_complete"] is False
        assert pc["prompt_on_login"] is True
        assert pc["required_missing"] == pc["required_total"]
        labels = [m["label"] for m in pc["missing_required"]]
        assert "Bank account number / IBAN" in labels
        assert "CNIC number" in labels

    def test_every_missing_field_says_why_it_is_wanted(self, env):
        c, auth = env["client"], env["auth"]
        pc = c.get("/api/v1/users/me/profile-completion",
                   headers=auth("employee")).json()
        # Asking for a national ID number without saying what it is for is how
        # a form gets abandoned.
        assert all(m["why"].strip() for m in pc["missing_required"])
        bank = next(m for m in pc["missing_required"] if m["field"] == "account_number")
        assert "Salary" in bank["why"]

    def test_filling_the_required_fields_stops_the_prompt(self, env):
        c, auth = env["client"], env["auth"]
        self._complete(env)
        pc = c.get("/api/v1/users/me/profile-completion",
                   headers=auth("employee")).json()
        assert pc["is_complete"] is True
        assert pc["prompt_on_login"] is False
        # Still not 100% — the recommended fields remain, and they do not block.
        assert pc["percent"] < 100
        assert pc["missing_recommended"]

    def test_the_login_payload_carries_it(self, env):
        """What the whole feature turns on: no second request at sign-in."""
        c = env["client"]
        r = c.post("/api/v1/auth/login",
                   data={"username": "emp@example.com", "password": "x"})
        assert r.status_code == 200, r.text
        assert r.json()["user"]["profile_completion"]["prompt_on_login"] is True

    def test_hr_can_see_who_still_has_gaps(self, env):
        c, auth, ids = env["client"], env["auth"], env["ids"]
        rows = c.get("/api/v1/users/incomplete-profiles", headers=auth("admin")).json()
        eve = next(r for r in rows if r["employee_id"] == ids["employee"])
        assert eve["required_missing"] > 0
        # Worst first: the person missing eight fields is a different
        # conversation from the one missing a branch code.
        assert [r["required_missing"] for r in rows] == sorted(
            (r["required_missing"] for r in rows), reverse=True)

    def test_that_list_is_not_for_employees(self, env):
        c, auth = env["client"], env["auth"]
        assert c.get("/api/v1/users/incomplete-profiles",
                     headers=auth("employee")).status_code == 403

    def test_the_sweep_notifies_once_not_every_run(self, env):
        from app.models.notification import Notification
        from app.utils.profile_completion import send_profile_reminders
        db = env["db"]

        first = send_profile_reminders(db)
        assert first["notified"] == 3        # nobody has filled anything in yet
        sent = db.query(Notification).filter(
            Notification.title == "Your profile is incomplete").count()
        assert sent == 3

        # Run again straight away: the interval has not elapsed.
        again = send_profile_reminders(db)
        assert again["notified"] == 0
        assert db.query(Notification).filter(
            Notification.title == "Your profile is incomplete").count() == 3

    def test_completing_a_profile_clears_the_marker(self, env):
        from app.models.user import User
        from app.utils.profile_completion import send_profile_reminders
        db = env["db"]
        send_profile_reminders(db)
        assert db.query(User).get(env["ids"]["employee"]).profile_reminder_sent_at

        self._complete(env)
        result = send_profile_reminders(db)
        assert result["already_complete"] >= 1
        # Cleared, so a field emptied later starts the cycle again rather than
        # being tolerated forever.
        assert db.query(User).get(env["ids"]["employee"]).profile_reminder_sent_at is None


class TestUnhandledExceptionsAreRecorded:
    """
    A 500 that only exists in a hosting platform's log stream is a 500 nobody
    can diagnose. These make one answerable from the database instead.
    """

    def test_the_caller_gets_a_reference_and_the_failure_is_stored(self, env, monkeypatch):
        from fastapi.testclient import TestClient
        from sqlalchemy.orm import sessionmaker
        import app.database as database
        from app.models.error_log import ErrorLog
        from app.main import app
        auth, db = env["auth"], env["db"]
        # The handler deliberately opens its own session — the request's is
        # unusable after an exception — so point that at the scratch database
        # rather than the real one.
        monkeypatch.setattr(database, "SessionLocal",
                            sessionmaker(bind=db.get_bind()))
        # And let the 500 be returned rather than re-raised at us.
        c = TestClient(app, raise_server_exceptions=False)

        @app.get("/api/v1/_boom_for_test")
        def _boom():
            raise RuntimeError("the thing that actually went wrong")
        try:
            r = c.get("/api/v1/_boom_for_test", headers=auth("employee"))
            assert r.status_code == 500
            ref = r.json().get("reference")
            assert ref, "no reference for the user to quote"
            assert ref in r.json()["detail"]

            row = db.query(ErrorLog).filter(ErrorLog.reference == ref).first()
            assert row is not None
            assert row.exception_type == "RuntimeError"
            assert "the thing that actually went wrong" in row.message
            assert "RuntimeError" in row.traceback
            assert row.path == "/api/v1/_boom_for_test"
            # Who hit it matters as much as what broke.
            assert row.actor_email == "emp@example.com"
        finally:
            app.router.routes = [x for x in app.router.routes
                                 if getattr(x, "path", None) != "/api/v1/_boom_for_test"]

    def test_the_failure_list_is_admin_only(self, env):
        c, auth = env["client"], env["auth"]
        assert c.get("/api/v1/admin/jobs/errors",
                     headers=auth("employee")).status_code == 403
        assert c.get("/api/v1/admin/jobs/errors",
                     headers=auth("admin")).status_code == 200


class TestAutoCheckoutCap:
    """
    Everyone was logged off at seven hours the first weekday a shift was
    assigned. The shift runs 12:00-20:00 with an hour's unpaid break, and the
    cap was reading its paid hours instead of its span.
    """

    def _shift(self, env, **kw):
        body = {"name": "Hardware Team Timings", "start_time": "12:00:00",
                "end_time": "20:00:00", "break_minutes": 60, "is_default": True}
        body.update(kw)
        return env["client"].post("/api/v1/shifts", headers=env["auth"]("admin"),
                                  json=body).json()

    def test_paid_hours_and_span_are_different_numbers(self, env):
        s = self._shift(env)
        # Seven paid, eight at work. Confusing the two is the whole bug.
        assert s["hours"] == 7.0
        from app.models.shift import Shift
        row = env["db"].query(Shift).get(s["id"])
        assert row.span_hours == 8.0

    def test_the_session_cap_uses_the_span(self, env):
        from app.utils.attendance_session import standard_hours_for
        c, auth, ids, db = env["client"], env["auth"], env["ids"], env["db"]
        s = self._shift(env)
        c.post("/api/v1/shifts/assignments", headers=auth("admin"), json={
            "employee_id": ids["employee"], "shift_id": s["id"],
            "effective_from": "2026-01-01"})
        # An unpaid break is time somebody is still at work, so the session
        # runs eight hours, not seven.
        assert standard_hours_for(db, ids["employee"]) == 8.0

    def test_a_default_shift_does_not_outrank_contracted_hours(self, env):
        from app.models.user import User
        from app.utils.attendance_session import standard_hours_for
        c, auth, ids, db = env["client"], env["auth"], env["ids"], env["db"]
        db.query(User).get(ids["employee"]).standard_hours = 4.0
        db.commit()
        self._shift(env)          # default, but nobody assigned to it
        # The company-wide default is the least specific thing here. A
        # part-timer must not inherit eight hours because nobody got round to
        # assigning them a pattern.
        assert standard_hours_for(db, ids["employee"]) == 4.0

    def test_an_assigned_shift_does_outrank_contracted_hours(self, env):
        from app.models.user import User
        from app.utils.attendance_session import standard_hours_for
        c, auth, ids, db = env["client"], env["auth"], env["ids"], env["db"]
        db.query(User).get(ids["employee"]).standard_hours = 4.0
        db.commit()
        s = self._shift(env)
        c.post("/api/v1/shifts/assignments", headers=auth("admin"), json={
            "employee_id": ids["employee"], "shift_id": s["id"],
            "effective_from": "2026-01-01"})
        # Somebody deliberately put this person on this pattern.
        assert standard_hours_for(db, ids["employee"]) == 8.0

    def test_nobody_falls_to_zero_on_a_day_the_shift_skips(self, env):
        from datetime import date
        from app.utils.attendance_session import standard_hours_for
        c, auth, ids, db = env["client"], env["auth"], env["ids"], env["db"]
        s = self._shift(env, days_of_week="1111100")   # Mon-Fri
        c.post("/api/v1/shifts/assignments", headers=auth("admin"), json={
            "employee_id": ids["employee"], "shift_id": s["id"],
            "effective_from": "2026-01-01"})
        # A Saturday. Working it is overtime, not an instant checkout.
        assert standard_hours_for(db, ids["employee"], on=date(2026, 9, 12)) == 8.0


class TestDeviceScansBecomeAttendance:
    """
    The device and the portal were two records of the same fact, and only one
    of them was what the dashboard, the hours and the payroll read. Somebody
    who badged the door and never opened the portal did not exist as far as
    their attendance was concerned.
    """

    def _scan(self, env, employee_id, day, first="03:38", last=None):
        from datetime import datetime
        from app.models.biometric import BiometricScan
        db = env["db"]
        fh, fm = (int(x) for x in first.split(":"))
        start = datetime.combine(day, datetime.min.time()).replace(hour=fh, minute=fm)
        end = start
        if last:
            lh, lm = (int(x) for x in last.split(":"))
            end = datetime.combine(day, datetime.min.time()).replace(hour=lh, minute=lm)
        db.add(BiometricScan(device_name="Tester", employee_id=employee_id,
                             scan_date=day, first_scan=start, last_scan=end,
                             single_scan=last is None))
        db.commit()
        return start, end

    def test_a_badge_with_no_portal_session_creates_one(self, env):
        from datetime import date
        from app.models.attendance import Attendance
        from app.utils.biometric import promote_scans_to_attendance
        db, ids = env["db"], env["ids"]
        day = date.today()
        start, _ = self._scan(env, ids["employee"], day)

        result = promote_scans_to_attendance(db, day)
        assert result["created"] == 1

        row = db.query(Attendance).filter(
            Attendance.employee_id == ids["employee"]).one()
        assert row.check_in_time == start
        # Marked, so it is distinguishable from something somebody typed.
        assert row.source == "device"
        assert "fingerprint device" in row.notes

    def test_an_existing_portal_session_is_left_alone(self, env):
        from datetime import date, datetime
        from app.models.attendance import Attendance
        from app.utils.biometric import promote_scans_to_attendance
        db, ids = env["db"], env["ids"]
        day = date.today()
        typed = Attendance(employee_id=ids["employee"],
                           check_in_time=datetime.combine(day, datetime.min.time()),
                           source="portal", notes="typed by hand")
        db.add(typed)
        db.commit()
        self._scan(env, ids["employee"], day)

        result = promote_scans_to_attendance(db, day)
        assert result["created"] == 0
        assert result["skipped"] == 1
        # One session, still the one the person entered. Raising a second
        # beside it would double-count the day.
        rows = db.query(Attendance).filter(
            Attendance.employee_id == ids["employee"]).all()
        assert len(rows) == 1
        assert rows[0].notes == "typed by hand"

    def test_running_it_twice_raises_nothing_the_second_time(self, env):
        from datetime import date
        from app.models.attendance import Attendance
        from app.utils.biometric import promote_scans_to_attendance
        db, ids = env["db"], env["ids"]
        day = date.today()
        self._scan(env, ids["employee"], day)
        assert promote_scans_to_attendance(db, day)["created"] == 1
        assert promote_scans_to_attendance(db, day)["created"] == 0
        assert db.query(Attendance).count() == 1

    def test_two_punches_close_the_session_one_does_not(self, env):
        from datetime import date, timedelta
        from app.models.attendance import Attendance
        from app.utils.biometric import promote_scans_to_attendance
        db, ids = env["db"], env["ids"]
        day = date.today() - timedelta(days=1)
        self._scan(env, ids["employee"], day, first="03:00", last="11:00")
        promote_scans_to_attendance(db, day)
        row = db.query(Attendance).one()
        assert row.check_out_time is not None
        assert row.duration_hours == 8.0

    def test_a_single_punch_leaves_the_departure_unguessed(self, env):
        from datetime import datetime, timedelta
        from app.models.attendance import Attendance
        from app.models.biometric import BiometricScan
        from app.utils.biometric import promote_scans_to_attendance
        db, ids = env["db"], env["ids"]
        # An hour ago, so the eight-hour cap cannot have elapsed. Pinning this
        # to a fixed clock time made the test pass all morning and fail every
        # afternoon, which says nothing about the code.
        punch = datetime.utcnow() - timedelta(hours=1)
        db.add(BiometricScan(device_name="Tester", employee_id=ids["employee"],
                             scan_date=punch.date(), first_scan=punch,
                             last_scan=punch, single_scan=True))
        db.commit()

        promote_scans_to_attendance(db, punch.date())
        row = db.query(Attendance).one()
        # Left open for the standard auto-checkout to close at the employee's
        # own hours — the same treatment as a portal check-in nobody closed.
        # Promotion itself never invents a departure time.
        assert row.check_out_time is None
        assert row.duration_hours is None

    def test_remote_and_exempt_people_are_skipped(self, env):
        from datetime import date
        from app.models.attendance import Attendance
        from app.models.user import User
        from app.utils.biometric import promote_scans_to_attendance
        db, ids = env["db"], env["ids"]
        u = db.query(User).get(ids["employee"])
        u.works_remotely = True
        db.commit()
        self._scan(env, ids["employee"], date.today())
        assert promote_scans_to_attendance(db, date.today())["created"] == 0
        assert db.query(Attendance).count() == 0
