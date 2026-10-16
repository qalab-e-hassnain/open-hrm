"""
Opening a leave year.

The bug this guards: uniqueness on employee_id alone meant the first leave
request of a new year failed outright, and nothing ever reset used days or
applied the carryover the policy allows.
"""
import pytest

from app.utils.leave_policy import LeavePolicyConfig


class Balance:
    def __init__(self, annual_total=14.0, annual_used=0.0, annual_carryover=0.0):
        self.annual_total = annual_total
        self.annual_used = annual_used
        self.annual_carryover = annual_carryover


def carryover_from(previous, cap=None):
    """The carryover rule, isolated from the database write around it."""
    cap = LeavePolicyConfig.ANNUAL_LEAVE_MAX_CARRYOVER if cap is None else cap
    unused = ((previous.annual_total or 0.0)
              + (previous.annual_carryover or 0.0)
              - (previous.annual_used or 0.0))
    return max(0.0, min(unused, float(cap)))


class TestCarryover:
    """
    Nothing carries over any more. A year's unused annual leave ends with the
    year, so these hold the new rule where they used to hold the old one.
    """

    def test_unused_days_do_not_carry(self):
        assert carryover_from(Balance(annual_total=14, annual_used=6)) == 0.0

    def test_a_full_year_untouched_still_carries_nothing(self):
        assert carryover_from(Balance(annual_total=14, annual_used=0)) == 0.0

    def test_the_policy_cap_is_zero(self):
        from app.utils.leave_policy import LeavePolicyConfig
        assert LeavePolicyConfig.ANNUAL_LEAVE_MAX_CARRYOVER == 0


class TestUsedDaysReset:
    def test_a_new_year_starts_from_zero_used(self):
        # The property that matters: consumption does not follow the employee
        # into the new year, only unused entitlement does.
        from app.models.leave_balance import LeaveBalance
        fresh = LeaveBalance(employee_id=1, year=2027)
        assert (fresh.annual_used or 0.0) == 0.0
        assert (fresh.sick_used or 0.0) == 0.0
        assert (fresh.personal_used or 0.0) == 0.0


class TestAnnualLeaveAccrual:
    """
    Annual leave is earned by the month and resets each January. Granting the
    full year on day one gave somebody who joined in July the same entitlement
    as somebody two years in.
    """

    def _joined(self, y, m, d=1):
        from datetime import datetime
        return datetime(y, m, d)

    def test_a_mid_year_joiner_earns_only_their_part(self):
        from datetime import date
        from app.utils.leave_policy import calculate_accrued_annual_leave
        # Joined 1 August, looked at on 1 December: August through November.
        # Four months is 4.67 days, rounded up to a whole day.
        assert calculate_accrued_annual_leave(
            self._joined(2026, 8), 2026, date(2026, 12, 1)) == 5.0

    def test_it_resets_in_january(self):
        from datetime import date
        from app.utils.leave_policy import calculate_accrued_annual_leave
        joined = self._joined(2024, 4)
        # Two years in, but only what this year has earned so far. Counting
        # months since joining instead gave twenty-four months' worth, silently
        # capped at the annual figure and wrong all year.
        assert calculate_accrued_annual_leave(joined, 2026, date(2026, 3, 15)) == 3.0
        assert calculate_accrued_annual_leave(joined, 2026, date(2026, 1, 2)) == 0.0

    def test_a_full_year_reaches_the_entitlement(self):
        from datetime import date
        from app.utils.leave_policy import (LeavePolicyConfig,
                                            calculate_accrued_annual_leave)
        got = calculate_accrued_annual_leave(
            self._joined(2020, 1), 2026, date(2026, 12, 31))
        assert got == float(LeavePolicyConfig.ANNUAL_LEAVE_PER_YEAR)

    def test_a_month_counts_on_the_day_it_ends(self):
        from datetime import date
        from app.utils.leave_policy import months_served_in_year
        joined = self._joined(2026, 6, 1)
        assert months_served_in_year(joined, 2026, date(2026, 6, 29)) == 0
        # June is over on the 30th, so it is earned on the 30th.
        assert months_served_in_year(joined, 2026, date(2026, 6, 30)) == 1

    def test_joining_part_way_through_a_month_does_not_earn_it(self):
        from datetime import date
        from app.utils.leave_policy import months_served_in_year
        # Joined 13 July, so July was never served in full.
        assert months_served_in_year(self._joined(2026, 7, 13), 2026,
                                     date(2026, 7, 31)) == 0
        assert months_served_in_year(self._joined(2026, 7, 13), 2026,
                                     date(2026, 8, 31)) == 1

    def test_someone_who_joins_next_year_has_nothing_yet(self):
        from datetime import date
        from app.utils.leave_policy import calculate_accrued_annual_leave
        assert calculate_accrued_annual_leave(
            self._joined(2027, 3), 2026, date(2026, 12, 1)) == 0.0

    def test_a_part_day_rounds_up_never_down(self):
        from datetime import date
        from app.utils.leave_policy import calculate_accrued_annual_leave
        # Leave is taken in whole days. One month is 1.17, which is two days,
        # not one — the rounding goes the employee's way.
        assert calculate_accrued_annual_leave(
            self._joined(2026, 8), 2026, date(2026, 9, 1)) == 2.0
        # A whole number stays put rather than gaining a day.
        assert calculate_accrued_annual_leave(
            self._joined(2026, 1), 2026, date(2026, 6, 30)) == 7.0

    def test_it_never_exceeds_the_annual_entitlement(self):
        from datetime import date
        from app.utils.leave_policy import (LeavePolicyConfig,
                                            calculate_accrued_annual_leave)
        # Rounding up must not push a full year past the policy figure.
        assert calculate_accrued_annual_leave(
            self._joined(2020, 1), 2026, date(2026, 12, 31)) == float(
                LeavePolicyConfig.ANNUAL_LEAVE_PER_YEAR)

    def test_nothing_carries_over(self):
        from app.utils.leave_policy import LeavePolicyConfig
        assert LeavePolicyConfig.ANNUAL_LEAVE_MAX_CARRYOVER == 0

    def test_the_sweep_never_pushes_somebody_overdrawn(self):
        """
        Somebody who has already taken more than they have accrued keeps what
        they took. A negative balance caused by a recalculation is a payroll
        conversation nobody asked for.
        """
        from datetime import date
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from sqlalchemy.pool import StaticPool
        from app.database import Base
        from app import models as _m            # noqa: F401
        from app.models.leave_balance import LeaveBalance
        from app.models.user import User, RoleEnum
        from app.utils.leave_accrual import refresh_balances

        engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                               poolclass=StaticPool)
        Base.metadata.create_all(engine)
        db = sessionmaker(bind=engine)()
        u = User(email="x@y.z", hashed_password="x", full_name="Taken Too Much",
                 role=RoleEnum.EMPLOYEE, is_active=True,
                 date_of_joining=self._joined(2026, 8))
        db.add(u); db.commit()
        db.add(LeaveBalance(employee_id=u.id, year=2026, annual_total=14.0,
                            annual_used=9.0))
        db.commit()

        result = refresh_balances(db, year=2026, on=date(2026, 9, 14))
        balance = db.query(LeaveBalance).one()
        assert balance.annual_total == 9.0     # floored at what was taken
        assert balance.annual_carryover == 0.0
        assert result["floored"]
        db.close()


class TestPersonalLeaveIsMonthly:
    """
    One day a month, use it or lose it. The annual pool let somebody save
    eleven months of personal leave and take a fortnight of it.
    """

    def _db(self):
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from sqlalchemy.pool import StaticPool
        from app.database import Base
        from app import models as _m          # noqa: F401
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                               poolclass=StaticPool)
        Base.metadata.create_all(engine)
        return sessionmaker(bind=engine)()

    def _employee(self, db):
        from datetime import datetime
        from app.models.user import RoleEnum, User
        u = User(email="p@q.r", hashed_password="x", full_name="Personal Tester",
                 role=RoleEnum.EMPLOYEE, is_active=True,
                 date_of_joining=datetime(2025, 1, 1))
        db.add(u); db.commit()
        return u

    def _leave(self, db, uid, start, end, status="approved", kind="casual"):
        from app.models.leave import Leave, LeaveStatusEnum, LeaveTypeEnum
        db.add(Leave(employee_id=uid, leave_type=LeaveTypeEnum(kind),
                     start_date=start, end_date=end, reason="x",
                     status=LeaveStatusEnum(status)))
        db.commit()

    def test_a_fresh_month_has_one_day(self):
        from app.utils.leave_accrual import personal_leave_remaining_in
        db = self._db(); u = self._employee(db)
        assert personal_leave_remaining_in(db, u.id, 9, 2026) == 1.0
        db.close()

    def test_using_the_day_leaves_nothing(self):
        from datetime import datetime
        from app.utils.leave_accrual import personal_leave_remaining_in
        db = self._db(); u = self._employee(db)
        self._leave(db, u.id, datetime(2026, 9, 8), datetime(2026, 9, 8))
        assert personal_leave_remaining_in(db, u.id, 9, 2026) == 0.0
        db.close()

    def test_nothing_carries_into_the_next_month(self):
        from app.utils.leave_accrual import personal_leave_remaining_in
        db = self._db(); u = self._employee(db)
        # Took none in August. September is still one day, not two.
        assert personal_leave_remaining_in(db, u.id, 9, 2026) == 1.0
        db.close()

    def test_a_used_month_does_not_block_the_next_one(self):
        from datetime import datetime
        from app.utils.leave_accrual import personal_leave_remaining_in
        db = self._db(); u = self._employee(db)
        self._leave(db, u.id, datetime(2026, 9, 8), datetime(2026, 9, 8))
        assert personal_leave_remaining_in(db, u.id, 9, 2026) == 0.0
        assert personal_leave_remaining_in(db, u.id, 10, 2026) == 1.0
        db.close()

    def test_a_pending_request_already_spends_the_day(self):
        from datetime import datetime
        from app.utils.leave_accrual import personal_leave_remaining_in
        db = self._db(); u = self._employee(db)
        self._leave(db, u.id, datetime(2026, 9, 8), datetime(2026, 9, 8),
                    status="pending")
        # Otherwise two separate requests that each fit the day could both be
        # filed, and together they do not.
        assert personal_leave_remaining_in(db, u.id, 9, 2026) == 0.0
        db.close()

    def test_a_rejected_request_gives_the_day_back(self):
        from datetime import datetime
        from app.utils.leave_accrual import personal_leave_remaining_in
        db = self._db(); u = self._employee(db)
        self._leave(db, u.id, datetime(2026, 9, 8), datetime(2026, 9, 8),
                    status="rejected")
        assert personal_leave_remaining_in(db, u.id, 9, 2026) == 1.0
        db.close()

    def test_other_leave_types_do_not_touch_it(self):
        from datetime import datetime
        from app.utils.leave_accrual import personal_leave_remaining_in
        db = self._db(); u = self._employee(db)
        self._leave(db, u.id, datetime(2026, 9, 8), datetime(2026, 9, 8), kind="annual")
        assert personal_leave_remaining_in(db, u.id, 9, 2026) == 1.0
        db.close()

    def test_a_request_spanning_a_month_end_is_clipped(self):
        from datetime import datetime
        from app.utils.leave_accrual import personal_leave_used_in
        db = self._db(); u = self._employee(db)
        # 30 September to 1 October: one working day in each month.
        self._leave(db, u.id, datetime(2026, 9, 30), datetime(2026, 10, 1))
        assert personal_leave_used_in(db, u.id, 9, 2026) == 1.0
        assert personal_leave_used_in(db, u.id, 10, 2026) == 1.0
        db.close()
