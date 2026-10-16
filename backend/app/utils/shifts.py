"""
Which shift applies to whom, on a given day.
"""
from datetime import date, datetime, time, timedelta
from typing import Optional

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models.shift import Shift, ShiftAssignment


def shift_for(db: Session, employee_id: int, on: Optional[date] = None) -> Optional[Shift]:
    """
    The shift in force for this employee on this date, if any.

    Falls back to the shift flagged as default, so a company that defines one
    pattern does not have to assign it to every employee individually.
    """
    on = on or date.today()
    assigned = assigned_shift_for(db, employee_id, on)
    if assigned:
        return assigned
    return default_shift(db)


def assigned_shift_for(db: Session, employee_id: int,
                       on: Optional[date] = None) -> Optional[Shift]:
    """
    Only a shift somebody was actually put on. No falling back to the default.

    The distinction matters wherever the default would otherwise outrank a
    fact about the individual — a part-timer contracted for four hours a day
    should not inherit the company's eight-hour pattern merely because nobody
    has assigned them one.
    """
    on = on or date.today()

    # This person's whole assignment history, fetched once per request and
    # then answered from memory. Asking the database per date turned a
    # thirty-day attendance report into thirty identical-shaped queries — most
    # of a five-second response — because each row asks about its own day.
    key = ("shift_assignments", employee_id)
    assignments = db.info.get(key)
    if assignments is None:
        assignments = (db.query(ShiftAssignment)
                       .filter(ShiftAssignment.employee_id == employee_id)
                       .order_by(ShiftAssignment.effective_from.desc())
                       .all())
        db.info[key] = assignments

    for assignment in assignments:          # newest first, as before
        if assignment.effective_from > on:
            continue
        if assignment.effective_to is not None and assignment.effective_to < on:
            continue
        if assignment.shift and assignment.shift.is_active:
            return assignment.shift
        return None
    return None


def prime_assignments(db: Session, employee_ids) -> None:
    """
    Load the assignment history for several people in one query.

    assigned_shift_for() caches per employee, which is right for one person's
    screen and still one query each across a whole company. A report over
    everybody calls this first and then finds every lookup already answered.
    Employees with no assignments are cached as empty, so they do not fall
    through and query again.
    """
    ids = [int(i) for i in employee_ids]
    missing = [i for i in ids if ("shift_assignments", i) not in db.info]
    if not missing:
        return
    found: dict = {i: [] for i in missing}
    for row in (db.query(ShiftAssignment)
                .filter(ShiftAssignment.employee_id.in_(missing))
                .order_by(ShiftAssignment.effective_from.desc()).all()):
        found[row.employee_id].append(row)
    for employee_id, rows in found.items():
        db.info[("shift_assignments", employee_id)] = rows


def forget_assignments(db: Session, employee_id: int) -> None:
    """
    Drop the cached assignment history for one employee.

    Call after changing their assignments. Nothing today reads the cache again
    inside the request that writes it, so this is insurance rather than a fix —
    but a cache that is only correct because of the order two lines happen to
    be in is a trap for whoever edits that endpoint next.
    """
    db.info.pop(("shift_assignments", employee_id), None)


def default_shift(db: Session) -> Optional[Shift]:
    """
    The company-wide pattern, cached for the life of the request.

    One row that cannot change mid-request, and every employee without an
    assigned shift falls back to it — so a report over the whole company
    asked for it once per person, and sometimes once per attendance day.
    """
    key = ("default_shift",)
    if key in db.info:
        return db.info[key]
    shift = (db.query(Shift)
             .filter(Shift.is_default.is_(True), Shift.is_active.is_(True))
             .first())
    db.info[key] = shift
    return shift


def expected_hours(db: Session, employee_id: int, on: Optional[date] = None,
                   paid: bool = True, assigned_only: bool = False) -> Optional[float]:
    """
    Hours the shift asks for on this date. None when no shift applies, and 0.0
    on a day the shift does not cover — a distinction the caller needs, because
    "no pattern defined" and "not a working day" mean opposite things.

    `paid` picks which figure. Pay wants the break excluded; an attendance
    session wants the whole span, because the break is unpaid but the person is
    still there.
    """
    shift = (assigned_shift_for(db, employee_id, on) if assigned_only
             else shift_for(db, employee_id, on))
    if not shift:
        return None
    on = on or date.today()
    if not shift.works_on(on):
        return 0.0
    return shift.hours if paid else shift.span_hours


def punctuality(shift: Shift, check_in: datetime, check_out: Optional[datetime],
                tz_offset_hours: int = 5) -> dict:
    """
    Late in, early out, measured against the shift and its grace period.

    Times are stored in UTC and the shift is written in local clock time, so
    the offset is applied here rather than leaving every caller to remember it.
    """
    grace = timedelta(minutes=shift.grace_minutes or 0)
    local_in = check_in + timedelta(hours=tz_offset_hours)
    day = local_in.date()
    scheduled_in = datetime.combine(day, shift.start_time)
    scheduled_out = datetime.combine(day, shift.end_time)
    if scheduled_out <= scheduled_in:
        scheduled_out += timedelta(days=1)

    late_by = local_in - (scheduled_in + grace)
    result = {
        "shift_name": shift.name,
        "scheduled_start": shift.start_time.strftime("%H:%M"),
        "scheduled_end": shift.end_time.strftime("%H:%M"),
        "is_late": late_by.total_seconds() > 0,
        "late_minutes": max(0, int(late_by.total_seconds() // 60)),
        "is_early_departure": False,
        "early_minutes": 0,
    }
    if check_out:
        local_out = check_out + timedelta(hours=tz_offset_hours)
        early_by = (scheduled_out - grace) - local_out
        result["is_early_departure"] = early_by.total_seconds() > 0
        result["early_minutes"] = max(0, int(early_by.total_seconds() // 60))
    return result
