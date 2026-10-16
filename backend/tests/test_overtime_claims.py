"""
Overtime is a claim, and a day is judged as a day.

Two rules that have to hold together. People forget to check out, so hours past
the standard day cannot be read as overtime — only a logged, signed-off claim
is. And because a long day is stored as several capped sessions, completion is
a question about the day, not about whichever row you happen to be looking at.
"""
from datetime import date, datetime

from app.utils.overtime import PAYROLL_CUTOFF_DAY, payroll_period_for, split_day_hours


class TestPayrollCutoff:
    """
    Payroll is prepared on the 25th, and the deadline is the EMPLOYEE's: it
    runs against the day they filed the claim, never the day it was approved.
    """

    def test_filed_before_the_cutoff_pays_in_the_month_worked(self):
        assert payroll_period_for(date(2026, 9, 10), datetime(2026, 9, 20)) == (9, 2026)

    def test_the_cutoff_day_itself_still_makes_the_run(self):
        assert payroll_period_for(
            date(2026, 9, 10),
            datetime(2026, 9, PAYROLL_CUTOFF_DAY, 23, 59)) == (9, 2026)

    def test_filed_after_the_cutoff_rolls_into_next_month(self):
        assert payroll_period_for(date(2026, 9, 10), datetime(2026, 9, 26)) == (10, 2026)

    def test_a_slow_approval_does_not_cost_the_employee_a_month(self):
        # Huzaifa's real claim: worked the 15th, filed the 16th, approved on
        # the 29th. He met the deadline; his manager did not. Keying this on
        # the approval date pushed his money into October, which is the bug
        # this test exists to keep out.
        assert payroll_period_for(date(2026, 9, 15), datetime(2026, 9, 16)) == (9, 2026)

    def test_filed_months_later_lands_in_that_months_run(self):
        assert payroll_period_for(date(2026, 9, 10), datetime(2026, 11, 3)) == (11, 2026)

    def test_december_rolls_the_year_over(self):
        assert payroll_period_for(date(2026, 12, 10), datetime(2026, 12, 28)) == (1, 2027)

    def test_a_claim_never_pays_earlier_than_the_work(self):
        # Filed ahead of the work date — it still waits for the month the
        # hours were actually put in.
        assert payroll_period_for(date(2026, 10, 3), datetime(2026, 9, 20)) == (10, 2026)


class TestClosedPeriodsRollForward:
    """
    Meeting the deadline is not enough on its own: a month whose payslips have
    gone out cannot absorb anything more. The claim is paid late, never lost.
    """

    def test_an_open_period_is_returned_unchanged(self):
        from app.utils.overtime import open_payroll_period
        assert open_payroll_period(self._db({}), 1, 9, 2026) == (9, 2026)

    def test_a_paid_period_rolls_to_the_next_open_one(self):
        from app.models.payroll import PayrollStatusEnum
        from app.utils.overtime import open_payroll_period
        db = self._db({(9, 2026): PayrollStatusEnum.PAID})
        assert open_payroll_period(db, 1, 9, 2026) == (10, 2026)

    def test_it_walks_past_several_settled_months(self):
        from app.models.payroll import PayrollStatusEnum
        from app.utils.overtime import open_payroll_period
        db = self._db({(9, 2026): PayrollStatusEnum.PAID,
                       (10, 2026): PayrollStatusEnum.PROCESSED,
                       (11, 2026): PayrollStatusEnum.PAID})
        assert open_payroll_period(db, 1, 9, 2026) == (12, 2026)

    def test_rolling_past_december_moves_into_the_new_year(self):
        from app.models.payroll import PayrollStatusEnum
        from app.utils.overtime import open_payroll_period
        db = self._db({(12, 2026): PayrollStatusEnum.PAID})
        assert open_payroll_period(db, 1, 12, 2026) == (1, 2027)

    def test_a_draft_payroll_is_still_open(self):
        from app.models.payroll import PayrollStatusEnum
        from app.utils.overtime import open_payroll_period
        db = self._db({(9, 2026): PayrollStatusEnum.DRAFT})
        assert open_payroll_period(db, 1, 9, 2026) == (9, 2026)

    @staticmethod
    def _db(settled):
        """A stand-in that answers "is this period settled" from a dict."""
        class _Q:
            def __init__(self, rows): self.rows, self.m, self.y = rows, None, None
            def filter(self, *clauses):
                for clause in clauses:
                    right = getattr(clause, "right", None)
                    value = getattr(right, "value", None)
                    key = getattr(getattr(clause, "left", None), "key", None)
                    if key == "month": self.m = value
                    elif key == "year": self.y = value
                return self
            def first(self):
                status = self.rows.get((self.m, self.y))
                if status is None: return None
                class _P: pass
                p = _P(); p.status = status
                return p
        class _Db:
            def query(self, *a, **k): return _Q(settled)
        return _Db()


class TestDayCompletion:
    """
    The three rows that prompted this: a full day followed by an evening
    session, where the evening session was being reported as a failed day.
    """

    def _day(self, sessions, required=8.0):
        parts = split_day_hours(sessions, required)
        total = round(sum(p["total_hours"] for p in parts), 2)
        return total, total + 0.001 >= required, parts

    def test_an_evening_session_after_a_full_day_is_part_of_a_completed_day(self):
        # Bilal, 10 September: 8h, then 2h55m in the evening.
        total, completed, parts = self._day([8.0, 2.92])
        assert (total, completed) == (10.92, True)
        # The evening row is all past the cap, but that is extra time, not a
        # short day — and not overtime until somebody logs it.
        assert parts[1]["overtime_hours"] == 2.92

    def test_a_short_second_session_still_completes_the_day(self):
        # Bilal, 7 September: 8h, then 51 minutes.
        assert self._day([8.0, 0.85])[1] is True

    def test_a_split_day_that_only_just_reaches_the_cap_is_complete(self):
        # Huzaifa, 7 September: 7h17m then 2h31m — neither row reaches 8 alone.
        total, completed, parts = self._day([7.28, 2.51])
        assert (total, completed) == (9.79, True)
        assert parts[0]["overtime_hours"] == 0.0
        assert parts[1]["regular_hours"] == 0.72

    def test_a_day_that_never_reaches_the_hours_is_not_complete(self):
        assert self._day([4.87])[1] is False
        assert self._day([7.17])[1] is False

    def test_exactly_the_standard_day_counts_as_complete(self):
        # Floating point must not make 8.0 read as short of 8.0.
        assert self._day([8.0])[1] is True
        assert self._day([2.67, 5.33])[1] is True

    def test_a_part_timers_shorter_day_is_judged_against_their_own_hours(self):
        assert self._day([4.0], required=4.0)[1] is True
        assert self._day([4.0], required=8.0)[1] is False
