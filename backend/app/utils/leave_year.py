"""
Opening an employee's leave balance for a year.

Balances are per employee per year. Nothing rolls the year over on a schedule —
the first request of a new year opens it — so this has to be safe to call
concurrently and idempotent when the row already exists.
"""
from sqlalchemy.orm import Session

from app.models.leave_balance import LeaveBalance
from app.utils.leave_policy import LeavePolicyConfig


def open_balance(db: Session, employee_id: int, year: int) -> LeaveBalance:
    """
    Return the employee's balance for `year`, creating it if this is the first
    time the year has been touched.

    Unused annual leave carries into the new year, capped by policy — currently
    10 days. Sick and personal do not carry: they are an allowance for the year,
    not an entitlement banked against it. Carryover is computed from the
    previous year only, so a long-dormant record cannot accumulate years of
    unused days at once.
    """
    existing = db.query(LeaveBalance).filter(
        LeaveBalance.employee_id == employee_id,
        LeaveBalance.year == year,
    ).first()
    if existing:
        return existing

    previous = db.query(LeaveBalance).filter(
        LeaveBalance.employee_id == employee_id,
        LeaveBalance.year == year - 1,
    ).first()

    carryover = 0.0
    if previous:
        unused = ((previous.annual_total or 0.0)
                  + (previous.annual_carryover or 0.0)
                  - (previous.annual_used or 0.0))
        carryover = max(0.0, min(unused, float(LeavePolicyConfig.ANNUAL_LEAVE_MAX_CARRYOVER)))

    balance = LeaveBalance(employee_id=employee_id, year=year,
                           annual_carryover=round(carryover, 2))
    db.add(balance)
    try:
        db.commit()
    except Exception:
        # Another request opened the same year first. Its row is as good as
        # ours, so take theirs rather than failing the request that raced.
        db.rollback()
        existing = db.query(LeaveBalance).filter(
            LeaveBalance.employee_id == employee_id,
            LeaveBalance.year == year,
        ).first()
        if existing:
            return existing
        raise
    db.refresh(balance)
    return balance
