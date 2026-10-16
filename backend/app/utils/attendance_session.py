"""
Working-day cap for attendance sessions.

A session is capped at the employee's standard hours (8h by default). Once the
cap is reached the session is closed automatically at `check_in + cap`, and the
employee has to check in again for the extra time — that second session is
overtime. Nothing is ever extended past the cap, so an employee who forgets to
check out is credited with 8 hours rather than the rest of the night.

There is no scheduler in this stack, so the cap is enforced lazily:
close_expired_sessions() runs at the start of every attendance read and write.
Any request that could otherwise observe a stale open session closes it first,
which makes the result identical to a cron job for every caller that matters
(the employee's own status, the admin's team view, reports and payroll).
"""
import logging
from datetime import datetime, timedelta
from typing import List, Optional

from sqlalchemy.orm import Session

from app.config import settings
from app.models.attendance import Attendance
from app.models.user import User

logger = logging.getLogger(__name__)

# Written into check_out_address so an auto-closed row is identifiable in the
# UI and in reports. Matches the convention the previous 24-hour cleanup used.
AUTO_CHECKOUT_ADDRESS = "Auto checkout after {hours:g} hours"


def is_auto_checkout(attendance: Attendance) -> bool:
    """True when this row was closed by the cap rather than by the employee."""
    return bool(attendance.check_out_address and attendance.check_out_address.startswith("Auto checkout"))


def standard_hours_for(db: Session, employee_id: int, on=None) -> float:
    """
    The employee's standard hours for a day, most specific answer first:
    a shift they were actually assigned, then their own contracted hours, then
    the company default shift, then the configured default.

    That order matters. A company-wide default shift is the least specific
    thing here, so it must not outrank a part-timer's own four hours simply
    because nobody has assigned them a pattern.

    A shift that does not cover this day is skipped rather than treated as zero
    hours — someone working a Saturday is doing overtime, not being checked out
    the instant they arrive.
    """
    try:
        from app.utils.shifts import expected_hours
        # The span, not the paid hours. This caps how long somebody may be
        # clocked in, and an unpaid break is time they are still at work.
        hours = expected_hours(db, employee_id, on, paid=False, assigned_only=True)
        if hours:
            return float(hours)
    except Exception:
        # A missing shifts table on a database that has not been migrated must
        # not stop anyone checking in.
        logger.debug("Could not read the shift for employee %s", employee_id, exc_info=True)

    employee = db.get(User, employee_id)   # identity map first
    if employee and employee.standard_hours:
        return float(employee.standard_hours)

    try:
        from app.utils.shifts import expected_hours
        hours = expected_hours(db, employee_id, on, paid=False)
        if hours:
            return float(hours)
    except Exception:
        logger.debug("Could not read the default shift", exc_info=True)

    return float(settings.STANDARD_WORK_HOURS_PER_DAY)


def close_expired_sessions(
    db: Session,
    employee_id: Optional[int] = None,
    now: Optional[datetime] = None,
) -> List[Attendance]:
    """
    Close every open session that has reached its cap.

    Args:
        db: database session
        employee_id: limit to one employee; None sweeps all open sessions
        now: reference time (defaults to utcnow)

    Returns:
        The rows that were closed, so the caller can notify or report on them.
    """
    if not settings.AUTO_CHECKOUT_AT_STANDARD_HOURS:
        return []

    now = now or datetime.utcnow()

    query = db.query(Attendance).filter(Attendance.check_out_time.is_(None))
    if employee_id is not None:
        query = query.filter(Attendance.employee_id == employee_id)
    open_sessions = query.all()
    if not open_sessions:
        return []

    hours_cache: dict[int, float] = {}
    closed: List[Attendance] = []

    for session in open_sessions:
        if session.check_in_time is None:
            continue
        if session.employee_id not in hours_cache:
            hours_cache[session.employee_id] = standard_hours_for(db, session.employee_id)
        cap = hours_cache[session.employee_id]

        deadline = session.check_in_time + timedelta(hours=cap)
        if now < deadline:
            continue

        session.check_out_time = deadline
        session.duration_hours = cap
        # No real check-out location exists, so the check-in coordinates stand in.
        session.check_out_latitude = session.check_in_latitude
        session.check_out_longitude = session.check_in_longitude
        session.check_out_address = AUTO_CHECKOUT_ADDRESS.format(hours=cap)
        closed.append(session)

    if closed:
        db.commit()
        for session in closed:
            db.refresh(session)

    return closed


def day_sessions(db: Session, employee_id: int, day_start: datetime) -> List[Attendance]:
    """All of one employee's sessions for a day, oldest first."""
    return (
        db.query(Attendance)
        .filter(
            Attendance.employee_id == employee_id,
            Attendance.check_in_time >= day_start,
            Attendance.check_in_time < day_start + timedelta(days=1),
        )
        .order_by(Attendance.check_in_time)
        .all()
    )
