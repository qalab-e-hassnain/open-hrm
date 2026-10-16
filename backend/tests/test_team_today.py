"""
One request for the whole team, instead of one request per person.

The team view asked for each person's status separately, on a ten-second
timer. Twenty people meant twenty requests a tick, each authenticating and
taking a database connection of its own; they raced each other for the pool
and some came back as 503s, so those rows showed the wrong status.

These tests pin down what the batched answer has to do: cover everyone the
caller is allowed to see and nobody else, say the same thing the per-person
endpoint says, and keep its promise when somebody has not checked in.
"""
from datetime import datetime, timedelta

from app.models.attendance import Attendance

URL = "/api/v1/attendance/team/today"


def _check_in(db, employee_id, start_hour=8.0, closed_after=None):
    """
    A session at a fixed hour of TODAY, optionally already checked out of.

    Anchored to today's midnight rather than to "N hours before now". The
    endpoint only looks at rows from today, so a session placed nine hours
    before the current time lands on yesterday whenever the suite runs before
    09:00 UTC, and the test fails for a reason that has nothing to do with
    the code. It did exactly that at 04:00.
    """
    day_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    start = day_start + timedelta(hours=start_hour)
    row = Attendance(employee_id=employee_id, check_in_time=start)
    if closed_after is not None:
        row.check_out_time = start + timedelta(hours=closed_after)
        row.duration_hours = closed_after
    db.add(row)
    db.commit()
    return row


class TestWhoIsIncluded:

    def test_an_admin_sees_everybody_active(self, env):
        body = env.get("admin", URL).json()
        ids = {entry["employee"]["id"] for entry in body["employees"]}
        assert ids >= {env.ids["eng"], env.ids["eng2"], env.ids["lead"]}

    def test_a_manager_sees_only_their_own_reports(self, env):
        body = env.get("lead", URL).json()
        ids = {entry["employee"]["id"] for entry in body["employees"]}
        assert ids == {env.ids["eng"], env.ids["eng2"]}
        assert env.ids["admin"] not in ids

    def test_an_employee_is_refused(self, env):
        assert env.get("eng", URL).status_code == 403


class TestWhatItSays:

    def test_somebody_who_has_not_checked_in(self, env):
        body = env.get("lead", URL).json()
        entry = next(e for e in body["employees"] if e["employee"]["id"] == env.ids["eng"])
        assert entry["status"] == "not_checked_in"
        assert entry["is_present"] is False
        assert entry["check_in_time"] is None

    def test_somebody_still_working(self, env):
        db = env.Session()
        _check_in(db, env.ids["eng"])
        db.close()

        body = env.get("lead", URL).json()
        entry = next(e for e in body["employees"] if e["employee"]["id"] == env.ids["eng"])
        assert entry["status"] == "checked_in"
        assert entry["is_present"] is True
        assert entry["check_out_time"] is None

    def test_somebody_who_has_finished_reports_their_hours(self, env):
        db = env.Session()
        _check_in(db, env.ids["eng2"], start_hour=9, closed_after=8)
        db.close()

        body = env.get("lead", URL).json()
        entry = next(e for e in body["employees"] if e["employee"]["id"] == env.ids["eng2"])
        assert entry["status"] == "checked_out"
        assert entry["working_hours"] == 8
        assert entry["check_out_time"] is not None

    def test_a_split_day_totals_every_session(self, env):
        """Hours cover the whole day, not just the last session of it."""
        db = env.Session()
        _check_in(db, env.ids["eng"], start_hour=8, closed_after=4)
        _check_in(db, env.ids["eng"], start_hour=13, closed_after=3)
        db.close()

        body = env.get("lead", URL).json()
        entry = next(e for e in body["employees"] if e["employee"]["id"] == env.ids["eng"])
        assert entry["sessions_today"] == 2
        assert entry["working_hours"] == 7


class TestItAgreesWithThePerPersonEndpoint:
    """
    The batched answer replaces twenty individual ones, so it has to say the
    same thing — otherwise the team view and the person's own row disagree.
    """

    def test_the_two_endpoints_match(self, env):
        db = env.Session()
        _check_in(db, env.ids["eng"], start_hour=9, closed_after=5)
        db.close()

        one = env.get("lead", f"/api/v1/attendance/employee/{env.ids['eng']}/today").json()
        many = env.get("lead", URL).json()
        mine = next(e for e in many["employees"] if e["employee"]["id"] == env.ids["eng"])

        for field in ("status", "is_present", "working_hours",
                      "check_in_time", "check_out_time", "sessions_today"):
            assert mine[field] == one[field], f"{field} differs between the two endpoints"
