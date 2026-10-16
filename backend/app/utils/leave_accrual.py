"""
Keeping the stored leave balance equal to what has actually been earned.

Annual leave accrues by the month and resets each January, so the entitlement
on somebody's record is not a constant — it grows through the year and drops
back on 1 January. Stored as a number and left alone, it is wrong within weeks:
every employee carried a flat fourteen days regardless of whether they joined
two years ago or last month.

So it is recomputed rather than set. A sweep brings every balance back in line
with the calendar, and it is safe to run as often as you like.
"""
import logging
from datetime import date

from sqlalchemy.orm import Session

from app.models.leave_balance import LeaveBalance
from app.models.user import User
from app.utils.leave_policy import (LeavePolicyConfig,
                                    calculate_accrued_annual_leave,
                                    months_served_in_year)

logger = logging.getLogger(__name__)


def refresh_balances(db: Session, year: int = None, on: date = None) -> dict:
    """
    Set every active employee's annual entitlement to what they have earned.

    Never reduces the entitlement below what somebody has already taken. A
    balance that reads as overdrawn is a payroll conversation nobody asked for,
    and it would be caused by this sweep rather than by the employee — so the
    floor is what they have used, and the case is reported instead.
    """
    year = year or (on or date.today()).year
    changed, created, floored = [], 0, []
    try:
        for user in db.query(User).filter(User.is_active.is_(True)).all():
            accrued = calculate_accrued_annual_leave(user.date_of_joining, year, on)

            balance = (db.query(LeaveBalance)
                       .filter(LeaveBalance.employee_id == user.id,
                               LeaveBalance.year == year).first())
            if balance is None:
                balance = LeaveBalance(employee_id=user.id, year=year,
                                       annual_used=0.0)
                db.add(balance)
                created += 1

            used = float(balance.annual_used or 0.0)
            entitlement = accrued
            if used > accrued:
                entitlement = used
                floored.append(f"{user.full_name}: has taken {used:g} but accrued "
                               f"{accrued:g} — left at {used:g}")

            before = float(balance.annual_total or 0.0)
            if abs(before - entitlement) > 0.001:
                changed.append({
                    "employee": user.full_name,
                    "months": months_served_in_year(user.date_of_joining, year, on),
                    "from": before, "to": entitlement, "used": used})
            balance.annual_total = entitlement
            # Nothing carries over; a year's unused leave ends with the year.
            balance.annual_carryover = 0.0

        db.commit()
        return {"year": year, "updated": len(changed), "created": created,
                "changes": changed, "floored": floored,
                "rate_per_year": LeavePolicyConfig.ANNUAL_LEAVE_PER_YEAR}
    except Exception:
        db.rollback()
        logger.exception("Leave accrual refresh failed")
        return {"year": year, "updated": 0, "created": 0, "error": True}


def personal_leave_used_in(db: Session, employee_id: int, month: int, year: int,
                           exclude_leave_id: int = None) -> float:
    """
    Personal ("casual") leave days falling inside one calendar month.

    Counts requests that are approved *or* still outstanding, because a pending
    request is a day the employee has already spoken for — otherwise somebody
    could file two separate requests that each fit the monthly day and together
    do not.

    A request spanning a month boundary is clipped: only the part inside this
    month counts against this month.
    """
    import calendar as _cal
    from datetime import datetime

    from app.models.leave import Leave
    from app.utils.leave_policy import calculate_working_days

    first = datetime(year, month, 1)
    last = datetime(year, month, _cal.monthrange(year, month)[1], 23, 59, 59)

    rows = (db.query(Leave)
            .filter(Leave.employee_id == employee_id,
                    Leave.start_date <= last, Leave.end_date >= first)
            .all())
    total = 0.0
    for leave in rows:
        if exclude_leave_id and leave.id == exclude_leave_id:
            continue
        kind = (leave.leave_type.value if hasattr(leave.leave_type, "value")
                else str(leave.leave_type).split(".")[-1].lower())
        state = (leave.status.value if hasattr(leave.status, "value")
                 else str(leave.status).split(".")[-1].lower())
        if kind != "casual" or state not in ("pending", "manager_approved", "approved"):
            continue
        total += calculate_working_days(max(leave.start_date, first),
                                        min(leave.end_date, last))
    return float(total)


def personal_leave_remaining_in(db: Session, employee_id: int, month: int,
                                year: int, exclude_leave_id: int = None) -> float:
    """
    What is left of this month's personal leave.

    One day a month, and it expires with the month. Nothing accumulates: an
    employee who took none in March does not have two days in April, which is
    the whole point of the change — the annual pool let somebody save eleven
    months of it and take a fortnight.
    """
    from app.utils.leave_policy import LeavePolicyConfig

    allowance = float(LeavePolicyConfig.PERSONAL_LEAVE_PER_MONTH)
    used = personal_leave_used_in(db, employee_id, month, year, exclude_leave_id)
    return max(0.0, allowance - used)
