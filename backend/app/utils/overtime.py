"""
Overtime calculation utilities
"""
import logging
from datetime import datetime, timedelta, date
from typing import Dict, List
from app.config import settings

logger = logging.getLogger(__name__)


def is_weekend(check_date: date) -> bool:
    """Check if date is weekend (Saturday=5, Sunday=6)"""
    return check_date.weekday() >= 5


_HOLIDAY_CACHE: dict = {}          # year -> frozenset[date]
_HOLIDAY_CACHE_TTL = 600           # seconds


def _holidays_for_year(year: int, db=None) -> frozenset:
    """
    Every holiday in a year, cached in process.

    is_holiday is called once per date when counting a leave's working days, so
    a query per call would open a database session per day of a date range —
    which is how the first version of this exhausted the connection pooler.
    Cached by year, with a short TTL so an admin adding a lunar date does not
    have to wait for a restart to see it take effect.
    """
    import time
    from app.models.holiday import Holiday

    hit = _HOLIDAY_CACHE.get(year)
    if hit and (time.monotonic() - hit[0]) < _HOLIDAY_CACHE_TTL:
        return hit[1]

    def _load(session):
        rows = session.query(Holiday.holiday_date).filter(
            Holiday.holiday_date >= date(year, 1, 1),
            Holiday.holiday_date <= date(year, 12, 31),
        ).all()
        return frozenset(r[0] for r in rows)

    try:
        if db is not None:
            found = _load(db)
        else:
            from app.database import SessionLocal
            session = SessionLocal()
            try:
                found = _load(session)
            finally:
                session.close()
    except Exception:
        # A holiday lookup failing must not take down attendance or leave. The
        # cost of answering "not a holiday" is one day classified as ordinary;
        # the cost of raising is a 500 on check-in.
        logger.exception("Holiday lookup failed for %s; treating the year as having none", year)
        return frozenset()

    _HOLIDAY_CACHE[year] = (time.monotonic(), found)
    return found


def clear_holiday_cache() -> None:
    """Drop the cache so a newly added holiday applies immediately."""
    _HOLIDAY_CACHE.clear()


def is_holiday(check_date: date, db=None) -> bool:
    """Whether a date is a public holiday the company observes."""
    return check_date in _holidays_for_year(check_date.year, db)


def calculate_overtime(
    check_in_time: datetime,
    check_out_time: datetime,
    standard_hours: float = None
) -> Dict[str, float]:
    """
    Calculate overtime hours from check-in and check-out times
    
    Args:
        check_in_time: Check-in datetime
        check_out_time: Check-out datetime (None if still working)
        standard_hours: Standard work hours (defaults to config)
    
    Returns:
        Dict with total_hours, regular_hours, overtime_hours
    """
    if standard_hours is None:
        standard_hours = settings.STANDARD_WORK_HOURS_PER_DAY
    
    if check_out_time is None:
        # Still working - calculate current hours
        check_out_time = datetime.utcnow()
    
    # Calculate total hours worked
    duration = check_out_time - check_in_time
    total_hours = duration.total_seconds() / 3600
    
    # Calculate regular and overtime hours
    regular_hours = min(total_hours, standard_hours)
    overtime_hours = max(0, total_hours - standard_hours)
    
    return {
        "total_hours": round(total_hours, 2),
        "regular_hours": round(regular_hours, 2),
        "overtime_hours": round(overtime_hours, 2)
    }


def split_day_hours(
    session_hours: List[float],
    standard_hours: float = None
) -> List[Dict[str, float]]:
    """
    Split one employee-day's sessions into regular vs overtime hours.

    `session_hours` must be the day's sessions in chronological order. The
    standard hours are consumed by the earliest sessions; everything after the
    cap is overtime.

    This exists because a working day is capped at `standard_hours` and closed
    automatically (see utils/attendance_session.py), so a single long day is
    stored as several attendance rows. Calling calculate_overtime() on each row
    on its own would report zero overtime for all of them — an 8h session plus
    a 3h session are individually under the cap, while the day really contains
    3 hours of overtime.

    Returns one dict per session, in the same order as the input.
    """
    if standard_hours is None:
        standard_hours = settings.STANDARD_WORK_HOURS_PER_DAY

    remaining_regular = max(standard_hours, 0.0)
    breakdown = []
    for hours in session_hours:
        hours = max(hours or 0.0, 0.0)
        regular = min(hours, remaining_regular)
        remaining_regular -= regular
        breakdown.append({
            "total_hours": round(hours, 2),
            "regular_hours": round(regular, 2),
            "overtime_hours": round(hours - regular, 2),
        })
    return breakdown


def session_hours(attendance, now: datetime = None) -> float:
    """
    Hours on a single attendance row. An open session is measured up to `now`,
    so a day in progress reports the time worked so far.
    """
    if attendance.check_in_time is None:
        return 0.0
    end = attendance.check_out_time or now or datetime.utcnow()
    return max((end - attendance.check_in_time).total_seconds() / 3600, 0.0)


def hours_by_attendance_id(
    attendances: List,
    standard_hours_for,
    now: datetime = None
) -> Dict[int, Dict[str, float]]:
    """
    Day-aware regular/overtime split for a set of attendance rows.

    Groups the rows by employee and calendar day, orders each day's sessions by
    check-in time, then splits them with split_day_hours(). Use this instead of
    calculate_overtime() whenever rows are processed in bulk (reports, payroll),
    or overtime created by a re-check-in after the auto-checkout is lost.

    Args:
        attendances: Attendance rows (any mix of employees and dates)
        standard_hours_for: callable(employee_id) -> standard hours for that employee
        now: reference time for still-open sessions (defaults to utcnow)

    Returns:
        {attendance.id: {"total_hours", "regular_hours", "overtime_hours"}}
    """
    grouped: Dict[tuple, List] = {}
    for attendance in attendances:
        if attendance.check_in_time is None:
            continue
        key = (attendance.employee_id, attendance.check_in_time.date())
        grouped.setdefault(key, []).append(attendance)

    result: Dict[int, Dict[str, float]] = {}
    for (employee_id, _day), day_sessions in grouped.items():
        day_sessions.sort(key=lambda a: a.check_in_time)
        breakdown = split_day_hours(
            [session_hours(a, now) for a in day_sessions],
            standard_hours_for(employee_id),
        )
        for attendance, hours in zip(day_sessions, breakdown):
            result[attendance.id] = hours
    return result


def standard_hours_resolver(db, default_hours: float = None):
    """
    Build a cached callable(employee_id) -> standard hours, for use as the
    `standard_hours_for` argument above. Caches so a report over hundreds of
    rows does not re-query the same employee.
    """
    from app.models.user import User

    if default_hours is None:
        default_hours = settings.STANDARD_WORK_HOURS_PER_DAY
    cache: Dict[int, float] = {}

    def resolve(employee_id: int) -> float:
        if employee_id not in cache:
            # get(), not query().first(): anything the caller has already
            # loaded is in the session's identity map, and this then costs
            # nothing instead of another round trip for a row we hold.
            employee = db.get(User, employee_id)
            cache[employee_id] = (
                employee.standard_hours if employee and employee.standard_hours else default_hours
            )
        return cache[employee_id]

    return resolve


def day_type_for(check_date: date) -> str:
    """Label a date as holiday / weekend / weekday (reporting only — overtime
    pay is flat, the day type carries no multiplier)."""
    if is_holiday(check_date):
        return "holiday"
    if is_weekend(check_date):
        return "weekend"
    return "weekday"


# Company standard: a full-time month is 160 working hours for the flat
# overtime formula (confirmed by admin). NOT 8 x actual days.
MONTHLY_WORKING_HOURS = 160.0

# 160 is 8 hours over 20 days. Naming the days separately is what lets a
# part-time employee's month be computed rather than assumed: somebody
# contracted for 4 hours works an 80-hour month, and dividing their salary by
# 160 would pay them half rate for every overtime hour.
WORKING_DAYS_PER_MONTH = 20.0


def monthly_hours_for(standard_hours_per_day: float = None) -> float:
    """The month's hours for someone working `standard_hours_per_day`."""
    if not standard_hours_per_day or standard_hours_per_day <= 0:
        return MONTHLY_WORKING_HOURS
    return standard_hours_per_day * WORKING_DAYS_PER_MONTH


def hourly_rate_from_salary(
    monthly_salary: float,
    monthly_working_hours: float = None,
    standard_hours_per_day: float = None,
) -> float:
    """
    The hourly rate: monthly BASE salary spread over that employee's month.

    Full time is 160 hours, so Rs 150,000 / 160 = Rs 937.50 an hour. A
    part-time employee's month is shorter — 4 hours a day is an 80-hour month —
    and using 160 for them would pay half rate for every overtime hour, which
    is the same error as paying them a full-time day rate for a half day, in
    the opposite direction.

    Pass `standard_hours_per_day` from the employee. Callers that do not know
    it get the full-time month, which is what every existing caller assumed
    anyway.

    This is the only hourly rate in the product. The users.hourly_rate column
    is not used for overtime — it is unset for everyone, which is why overtime
    money used to come out as zero.
    """
    if not monthly_salary or monthly_salary <= 0:
        return 0.0
    hours = monthly_working_hours or monthly_hours_for(standard_hours_per_day)
    if not hours or hours <= 0:
        return 0.0
    return round(monthly_salary / hours, 2)


def calculate_unpaid_leave_deduction(
    monthly_salary: float,
    unpaid_working_days: float,
    hours_per_day: float = 8.0,
    standard_hours_per_day: float = None,
) -> float:
    """
    Salary not earned during approved unpaid leave.

    Company formula: (basic salary / 160) x 8 hours x working days.
    The result is capped at the basic salary so leave can never make the
    employee's earned basic pay negative.
    """
    if not unpaid_working_days or unpaid_working_days <= 0:
        return 0.0
    deduction = (
        hourly_rate_from_salary(monthly_salary,
                                standard_hours_per_day=standard_hours_per_day)
        * hours_per_day
        * unpaid_working_days
    )
    return round(min(deduction, max(monthly_salary or 0.0, 0.0)), 2)


def calculate_flat_overtime_pay(
    monthly_salary: float,
    overtime_hours: float,
    monthly_working_hours: float = None,
    standard_hours_per_day: float = None,
) -> float:
    """
    Flat overtime pay — NO weekday/weekend/holiday multipliers:

        overtime pay = (monthly salary / monthly working hours) * extra hours

    `monthly_salary` is the employee's contracted monthly (base) salary.
    """
    if not overtime_hours or overtime_hours <= 0:
        return 0.0
    rate = hourly_rate_from_salary(monthly_salary, monthly_working_hours,
                                   standard_hours_per_day)
    return round(rate * overtime_hours, 2)


def flat_overtime_estimate(
    monthly_salary: float,
    overtime_hours: float,
    check_date: date = None,
    monthly_working_hours: float = MONTHLY_WORKING_HOURS,
) -> Dict[str, float]:
    """
    Per-record overtime money, using the same formula payroll pays out.

    `overtime_rate` is PKR per hour — NOT a multiplier — so rate x hours always
    reconciles with the amount shown next to it.
    """
    return {
        "overtime_rate": hourly_rate_from_salary(monthly_salary, monthly_working_hours),
        "overtime_bonus": calculate_flat_overtime_pay(monthly_salary, overtime_hours, monthly_working_hours),
        "day_type": day_type_for(check_date) if check_date else "weekday",
    }


def get_week_start_end(target_date: date = None) -> tuple:
    """
    Get start and end of week (Monday to Sunday)
    
    Args:
        target_date: Target date (defaults to today)
    
    Returns:
        Tuple of (week_start, week_end) as datetime objects
    """
    if target_date is None:
        target_date = date.today()
    
    # Get Monday of the week
    week_start = target_date - timedelta(days=target_date.weekday())
    # Get Sunday of the week
    week_end = week_start + timedelta(days=6)
    
    # Convert to datetime
    week_start_dt = datetime.combine(week_start, datetime.min.time())
    week_end_dt = datetime.combine(week_end, datetime.max.time())
    
    return week_start_dt, week_end_dt


def format_hours(hours: float) -> str:
    """Format hours as 'Xh Ym' (e.g., '8h 45m')"""
    h = int(hours)
    m = int((hours - h) * 60)
    return f"{h}h {m}m"


def get_day_name(check_date: date) -> str:
    """Get day name from date"""
    days = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
    return days[check_date.weekday()]


# ---------------------------------------------------------------------------
# Overtime is a claim, not a measurement
# ---------------------------------------------------------------------------

# Payroll for a month is prepared on the 25th. A claim signed off after that
# has missed the run, so it is paid with the following month instead of
# quietly reopening a period whose payslips are already out.
PAYROLL_CUTOFF_DAY = 25


def payroll_period_for(work_date, filed_on) -> tuple:
    """
    The (month, year) a claim is paid in, from when the EMPLOYEE filed it.

    The 25th is the employee's deadline, not the approver's. Someone who logs
    their hours on the 16th has done everything asked of them; if their manager
    signs it off on the 29th, that delay belongs to the company and must not
    push the employee's money into next month. Keying this on the approval
    date did exactly that, and it is the one thing the deadline must never do.

    A claim filed after the 25th has missed the run and is paid next month.
    The result is the later of that and the month worked, so a claim never
    lands in a period earlier than the work itself.

    Whether the period is still open is a separate question — a claim filed in
    time for a month whose payslips have already gone out cannot be paid there
    however blameless the employee. See open_payroll_period().
    """
    work_month, work_year = work_date.month, work_date.year

    filed_month, filed_year = filed_on.month, filed_on.year
    if filed_on.day > PAYROLL_CUTOFF_DAY:
        filed_month += 1
        if filed_month > 12:
            filed_month, filed_year = 1, filed_year + 1

    if (filed_year, filed_month) > (work_year, work_month):
        return filed_month, filed_year
    return work_month, work_year


def open_payroll_period(db, employee_id: int, month: int, year: int) -> tuple:
    """
    The first period at or after (month, year) whose payroll can still change.

    A claim that met its deadline still cannot be paid into a month already
    settled — the payslip is out and the money has moved. Rather than silently
    dropping it, this walks forward to the next period that has not been
    processed or paid, so it is paid late rather than not at all.
    """
    from app.models.payroll import Payroll, PayrollStatusEnum

    for _ in range(12):
        payroll = (db.query(Payroll)
                   .filter(Payroll.employee_id == employee_id,
                           Payroll.month == month, Payroll.year == year)
                   .first())
        if payroll is None or payroll.status not in (PayrollStatusEnum.PROCESSED,
                                                     PayrollStatusEnum.PAID):
            return month, year
        month += 1
        if month > 12:
            month, year = 1, year + 1
    return month, year


def approved_overtime_by_day(db, attendances) -> Dict[tuple, float]:
    """
    Approved overtime hours keyed by (employee_id, date), for the days covered
    by `attendances`.

    Hours past the standard day are a fact about the clock; they are not
    overtime. People forget to check out, and a forgotten checkout that reads
    as three hours of overtime is a pay expectation nobody agreed to. Overtime
    exists once the employee logs it and the line manager and HR both sign, so
    this reads the signed claims rather than the clock.
    """
    from datetime import time as _time

    from app.models.time_log import (TimeLogKindEnum, TimeLogRequest,
                                     TimeLogStatusEnum)

    keys = {(a.employee_id, a.check_in_time.date())
            for a in attendances if a.check_in_time is not None}
    if not keys:
        return {}

    days = sorted({day for _, day in keys})
    try:
        rows = (db.query(TimeLogRequest)
                .filter(TimeLogRequest.kind == TimeLogKindEnum.OVERTIME,
                        TimeLogRequest.status == TimeLogStatusEnum.APPROVED,
                        TimeLogRequest.employee_id.in_({e for e, _ in keys}),
                        TimeLogRequest.work_date >= datetime.combine(days[0], _time.min),
                        TimeLogRequest.work_date <= datetime.combine(days[-1], _time.max))
                .all())
    except Exception:
        # An attendance list must not 500 because the time-log table is
        # missing on a database that has not been migrated.
        logger.exception("Could not read approved overtime claims")
        return {}

    claimed: Dict[tuple, float] = {}
    for row in rows:
        key = (row.employee_id, row.work_date.date())
        if key in keys:
            claimed[key] = claimed.get(key, 0.0) + float(row.hours or 0.0)
    return claimed


def day_context(db, attendances, standard_hours_for=None, now=None) -> Dict[int, Dict]:
    """
    Per-row hours plus the day they belong to, for a page of attendance rows.

    Two things a single row cannot answer on its own:

    Whether the day was completed. A working day is capped and closed
    automatically, so one day is often several rows — an 8h session and a 3h
    evening session. Judging each row against the standard day marks the
    evening one "not completed" when the person had in fact worked eleven
    hours.

    How the hours split. The cap is consumed by the earliest sessions, so the
    split only means anything across the whole day.

    Sibling sessions are loaded for every employee-day the page touches, since
    a page boundary can fall between two sessions of one day and pagination is
    not supposed to change what somebody worked.

    Returns {attendance.id: {total_hours, regular_hours, extra_hours,
             day_total_hours, day_required_hours, day_completed}}.
    """
    from datetime import time as _time

    from app.models.attendance import Attendance

    rows = [a for a in attendances if a.check_in_time is not None]
    if not rows:
        return {}
    if standard_hours_for is None:
        standard_hours_for = standard_hours_resolver(db)

    wanted = {(a.employee_id, a.check_in_time.date()) for a in rows}
    days = sorted({day for _, day in wanted})

    siblings = (db.query(Attendance)
                .filter(Attendance.employee_id.in_({e for e, _ in wanted}),
                        Attendance.check_in_time >= datetime.combine(days[0], _time.min),
                        Attendance.check_in_time <= datetime.combine(days[-1], _time.max))
                .all())

    grouped: Dict[tuple, List] = {}
    for session in siblings:
        key = (session.employee_id, session.check_in_time.date())
        if key in wanted:
            grouped.setdefault(key, []).append(session)

    on_page = {a.id for a in rows}
    result: Dict[int, Dict] = {}
    for (employee_id, day), sessions in grouped.items():
        sessions.sort(key=lambda a: a.check_in_time)
        required = standard_hours_for(employee_id)
        breakdown = split_day_hours(
            [session_hours(a, now) for a in sessions], required)
        day_total = round(sum(part["total_hours"] for part in breakdown), 2)
        for session, hours in zip(sessions, breakdown):
            if session.id not in on_page:
                continue
            result[session.id] = {
                "total_hours": hours["total_hours"],
                "regular_hours": hours["regular_hours"],
                # Named for what it is: time past the standard day that nobody
                # has claimed. It becomes overtime only once it is logged and
                # signed off — see approved_overtime_by_day().
                "extra_hours": hours["overtime_hours"],
                "day_total_hours": day_total,
                "day_required_hours": round(required, 2),
                "day_completed": day_total + 0.001 >= required,
            }
    return result
