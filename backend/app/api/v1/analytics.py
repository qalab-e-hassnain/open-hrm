"""
Company-level numbers, assembled from data the portal already holds.

No new tables and nothing recomputed on write: every figure here is a query
over records that exist for their own reasons. Restricted to admin and HR —
headcount, attrition and payroll cost are not directory information.
"""
import calendar
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from app.api.deps import require_admin_or_hr
from app.config import settings
from app.database import get_db
from app.models.leave import Leave, LeaveStatusEnum
from app.models.leave_balance import LeaveBalance
from app.models.lifecycle import Resignation, ResignationStatusEnum
from app.models.overtime import OvertimeRecord, OvertimeStatusEnum
from app.models.payroll import Payroll
from app.models.user import User
from app.utils.leave_policy import calculate_working_days
from app.utils.overtime import WORKING_DAYS_PER_MONTH

router = APIRouter()


def _month_keys(months: int) -> List[str]:
    """The last `months` months, oldest first, as YYYY-MM."""
    today = date.today()
    keys = []
    year, month = today.year, today.month
    for _ in range(months):
        keys.append(f"{year:04d}-{month:02d}")
        month -= 1
        if month == 0:
            year, month = year - 1, 12
    return list(reversed(keys))


def _month_bounds(key: str) -> tuple:
    year, month = (int(p) for p in key.split("-"))
    last = calendar.monthrange(year, month)[1]
    return date(year, month, 1), date(year, month, last)


class Point(BaseModel):
    label: str
    value: float


class Overview(BaseModel):
    headcount: int
    headcount_by_department: List[Point]
    headcount_by_role: List[Point]
    joiners_this_year: int
    leavers_this_year: int
    attrition_rate_percent: float
    open_resignations: int
    on_probation: int
    average_tenure_years: float
    # Full-year entitlement not yet taken — what the liability becomes by
    # December if nobody takes any more leave.
    leave_liability_days: float
    leave_liability_amount: float
    # Earned but untaken as at today, pro-rated by service. The accounting one.
    leave_accrued_days: float = 0.0
    leave_accrued_amount: float = 0.0
    pending_leave_requests: int
    pending_overtime_requests: int
    # Shape of the workforce, not just its size.
    part_time_count: int = 0
    remote_count: int = 0
    tenure_bands: List[Point] = Field(default_factory=list)
    gender_split: List[Point] = Field(default_factory=list)
    # Money the company is owed or has out in the field.
    advances_outstanding: float = 0.0
    assets_issued_count: int = 0
    assets_issued_value: float = 0.0
    currency: str = "PKR"
    generated_at: datetime


@router.get("/overview", response_model=Overview)
def overview(db: Session = Depends(get_db),
             current_user: User = Depends(require_admin_or_hr)):
    """The headline numbers, for the top of the HR dashboard."""
    from app.models.probation import ProbationStatusEnum
    from app.utils.probation import current_status

    active = db.query(User).filter(User.is_active.is_(True)).all()
    today = date.today()
    year_start = date(today.year, 1, 1)

    by_dept: Dict[str, int] = {}
    by_role: Dict[str, int] = {}
    tenures, probationers = [], 0
    for u in active:
        by_dept[u.department or "Unassigned"] = by_dept.get(u.department or "Unassigned", 0) + 1
        role = u.role.value if hasattr(u.role, "value") else str(u.role)
        by_role[role] = by_role.get(role, 0) + 1
        if u.date_of_joining:
            joined = u.date_of_joining.date() if isinstance(u.date_of_joining, datetime) \
                else u.date_of_joining
            tenures.append((today - joined).days / 365.25)
        if current_status(u) == ProbationStatusEnum.IN_PROBATION.value:
            probationers += 1

    joiners = sum(1 for u in db.query(User).all()
                  if u.date_of_joining and (
                      u.date_of_joining.date() if isinstance(u.date_of_joining, datetime)
                      else u.date_of_joining) >= year_start)
    leavers = (db.query(Resignation)
               .filter(Resignation.status == ResignationStatusEnum.COMPLETED,
                       Resignation.completed_at >= datetime(today.year, 1, 1))
               .count())
    # Leavers over average headcount for the period — the standard reading, and
    # the reason it is stated as a rate rather than a count in a company where
    # one departure is seven percent.
    average_headcount = len(active) + (leavers / 2.0)
    attrition = round((leavers / average_headcount) * 100, 1) if average_headcount else 0.0

    # Two different numbers, and the difference matters. What somebody would be
    # owed if the whole year's entitlement were cashed out today is not what the
    # company owes today: in September, three months of that entitlement has not
    # been earned yet. The accrued figure is the one that belongs on a balance
    # sheet; the full-year one is what the liability grows to by December.
    liability_days = liability_amount = 0.0
    accrued_days = accrued_amount = 0.0
    year_start = date(today.year, 1, 1)
    # Every balance in one query. Fetched per employee this was twenty round
    # trips for twenty people, and four seconds of a ten-second page.
    balances = {b.employee_id: b for b in
                db.query(LeaveBalance)
                .filter(LeaveBalance.employee_id.in_([u.id for u in active]),
                        LeaveBalance.year == today.year).all()} if active else {}
    for u in active:
        balance = balances.get(u.id)
        if not balance:
            continue
        entitlement = float(balance.annual_total or 0.0)
        if settings.ENCASH_ANNUAL_CARRYOVER:
            entitlement += float(balance.annual_carryover or 0.0)
        used = float(balance.annual_used or 0.0)
        day_rate = float(u.base_salary or 0.0) / WORKING_DAYS_PER_MONTH

        unused = max(0.0, entitlement - used)
        liability_days += unused
        liability_amount += unused * day_rate

        # Earned so far, pro-rated over the part of the leave year this person
        # has actually worked — somebody who joined in June has not accrued
        # eight months of leave.
        joined = u.date_of_joining
        joined = (joined.date() if isinstance(joined, datetime) else joined) or year_start
        started = max(joined, year_start)
        worked_days = max(0, (today - started).days)
        fraction = min(1.0, worked_days / 365.0)
        earned = round(entitlement * fraction, 2)
        accrued = max(0.0, earned - used)
        accrued_days += accrued
        accrued_amount += accrued * day_rate

    # --- shape of the workforce ---------------------------------------
    bands = {"Under 1 year": 0, "1 – 2 years": 0, "2 – 3 years": 0, "3 years +": 0}
    for years in tenures:
        if years < 1:
            bands["Under 1 year"] += 1
        elif years < 2:
            bands["1 – 2 years"] += 1
        elif years < 3:
            bands["2 – 3 years"] += 1
        else:
            bands["3 years +"] += 1

    genders: Dict[str, int] = {}
    for u in active:
        key = (u.gender.value if hasattr(u.gender, "value") else u.gender) or "not recorded"
        genders[key] = genders.get(key, 0) + 1

    # --- money out in the field ----------------------------------------
    from app.models.inventory import InvItem
    from app.utils.loans import outstanding_for_many

    advances = round(sum(outstanding_for_many(
        db, [u.id for u in active]).values()), 2)
    issued = (db.query(InvItem)
              .filter(InvItem.current_holder_id.isnot(None)).all())
    issued_value = round(sum(
        float(i.replacement_cost or i.purchase_cost or 0.0)
        for i in issued), 2)

    return Overview(
        headcount=len(active),
        part_time_count=sum(1 for u in active
                            if u.standard_hours and u.standard_hours < 8),
        remote_count=sum(1 for u in active if getattr(u, "works_remotely", False)),
        tenure_bands=[Point(label=k, value=v) for k, v in bands.items() if v],
        gender_split=[Point(label=k, value=v) for k, v in
                      sorted(genders.items(), key=lambda i: -i[1])],
        advances_outstanding=advances,
        assets_issued_count=len(issued),
        assets_issued_value=issued_value,
        currency=settings.CURRENCY,
        headcount_by_department=[Point(label=k, value=v) for k, v in
                                 sorted(by_dept.items(), key=lambda i: -i[1])],
        headcount_by_role=[Point(label=k, value=v) for k, v in
                           sorted(by_role.items(), key=lambda i: -i[1])],
        joiners_this_year=joiners,
        leavers_this_year=leavers,
        attrition_rate_percent=attrition,
        open_resignations=db.query(Resignation).filter(
            Resignation.status.in_([ResignationStatusEnum.SUBMITTED,
                                    ResignationStatusEnum.MANAGER_APPROVED,
                                    ResignationStatusEnum.CEO_APPROVED,
                                    ResignationStatusEnum.CLEARED])).count(),
        on_probation=probationers,
        average_tenure_years=round(sum(tenures) / len(tenures), 2) if tenures else 0.0,
        leave_liability_days=round(liability_days, 1),
        leave_liability_amount=round(liability_amount, 2),
        leave_accrued_days=round(accrued_days, 1),
        leave_accrued_amount=round(accrued_amount, 2),
        pending_leave_requests=db.query(Leave).filter(
            Leave.status == LeaveStatusEnum.PENDING).count(),
        pending_overtime_requests=db.query(OvertimeRecord).filter(
            OvertimeRecord.status == OvertimeStatusEnum.PENDING).count(),
        generated_at=datetime.utcnow(),
    )


class HeadcountSeries(BaseModel):
    months: List[str]
    headcount: List[float]
    joiners: List[float]
    leavers: List[float]


@router.get("/headcount", response_model=HeadcountSeries)
def headcount_trend(months: int = Query(12, ge=1, le=60),
                    db: Session = Depends(get_db),
                    current_user: User = Depends(require_admin_or_hr)):
    """
    Headcount at each month end, with joiners and leavers.

    Reconstructed from joining dates and completed resignations rather than
    from a snapshot table, so it is right for history the portal never
    observed live — which is most of it.
    """
    keys = _month_keys(months)
    everyone = db.query(User).all()
    exits = {}
    for r in (db.query(Resignation)
              .filter(Resignation.status == ResignationStatusEnum.COMPLETED).all()):
        last = r.agreed_last_working_day or r.proposed_last_working_day
        if last:
            exits[r.employee_id] = last

    headcount, joiners, leavers = [], [], []
    for key in keys:
        start, end = _month_bounds(key)
        count = j = l = 0
        for u in everyone:
            joined = u.date_of_joining
            if not joined:
                continue
            joined = joined.date() if isinstance(joined, datetime) else joined
            left = exits.get(u.id)
            if joined <= end and (left is None or left > end):
                count += 1
            if start <= joined <= end:
                j += 1
            if left and start <= left <= end:
                l += 1
        headcount.append(float(count))
        joiners.append(float(j))
        leavers.append(float(l))
    return HeadcountSeries(months=keys, headcount=headcount,
                           joiners=joiners, leavers=leavers)


class LeaveStats(BaseModel):
    year: int
    approved_days: float
    working_days_available: float
    absence_rate_percent: float
    by_type: List[Point]
    by_month: List[Point]
    top_takers: List[Point]
    average_decision_days: Optional[float] = None


@router.get("/leave", response_model=LeaveStats)
def leave_stats(year: Optional[int] = None, db: Session = Depends(get_db),
                current_user: User = Depends(require_admin_or_hr)):
    year = year or date.today().year
    start = datetime(year, 1, 1)
    end = datetime(year, 12, 31, 23, 59, 59)
    leaves = (db.query(Leave)
              .filter(Leave.status == LeaveStatusEnum.APPROVED,
                      Leave.start_date >= start, Leave.start_date <= end).all())

    by_type: Dict[str, float] = {}
    by_month: Dict[str, float] = {}
    by_person: Dict[int, float] = {}
    decision_days = []
    total_days = 0.0
    for lv in leaves:
        days = float(lv.total_days or 0)
        if not days:
            days = float(calculate_working_days(lv.start_date, lv.end_date))
        total_days += days
        kind = lv.leave_type.value if hasattr(lv.leave_type, "value") else str(lv.leave_type)
        by_type[kind] = by_type.get(kind, 0.0) + days
        key = f"{lv.start_date.year:04d}-{lv.start_date.month:02d}"
        by_month[key] = by_month.get(key, 0.0) + days
        by_person[lv.employee_id] = by_person.get(lv.employee_id, 0.0) + days
        if lv.approved_at and lv.created_at:
            decision_days.append((lv.approved_at - lv.created_at).total_seconds() / 86400)

    active = db.query(User).filter(User.is_active.is_(True)).count()
    # Working days in the year so far, not the whole year — an absence rate for
    # a year that has not happened yet reads far too low.
    horizon = min(date(year, 12, 31), date.today()) if year == date.today().year \
        else date(year, 12, 31)
    available = float(calculate_working_days(date(year, 1, 1), horizon) * active)

    names = {u.id: u.full_name for u in db.query(User).all()}
    return LeaveStats(
        year=year,
        approved_days=round(total_days, 1),
        working_days_available=available,
        absence_rate_percent=round((total_days / available) * 100, 2) if available else 0.0,
        by_type=[Point(label=k, value=round(v, 1)) for k, v in
                 sorted(by_type.items(), key=lambda i: -i[1])],
        by_month=[Point(label=k, value=round(v, 1)) for k, v in sorted(by_month.items())],
        top_takers=[Point(label=names.get(k, f"#{k}"), value=round(v, 1)) for k, v in
                    sorted(by_person.items(), key=lambda i: -i[1])[:10]],
        average_decision_days=round(sum(decision_days) / len(decision_days), 1)
        if decision_days else None,
    )


class PayrollSeries(BaseModel):
    months: List[str]
    gross: List[float]
    net: List[float]
    tax: List[float]
    overtime: List[float]
    headcount_paid: List[float]
    currency: str
    # True while this month's payroll has not been run. The series stops at the
    # last month that was actually paid rather than plotting the current one at
    # zero, which drew a cliff on the chart every month until payday.
    current_month_pending: bool = False
    tax_percent_of_gross: Optional[float] = None
    average_cost_per_head: Optional[float] = None


@router.get("/payroll", response_model=PayrollSeries)
def payroll_trend(months: int = Query(12, ge=1, le=60),
                  db: Session = Depends(get_db),
                  current_user: User = Depends(require_admin_or_hr)):
    """What the company actually paid, month by month."""
    keys = _month_keys(months)

    # One read for every month on the chart, bucketed here. It was a query per
    # month — twelve round trips to a database a long way away, each returning
    # rows from the same table, which was most of the three and a half seconds
    # this endpoint took.
    wanted = {tuple(int(p) for p in key.split("-")) for key in keys}
    by_period: Dict[tuple, list] = {period: [] for period in wanted}
    if wanted:
        years = {year for year, _ in wanted}
        for row in db.query(Payroll).filter(Payroll.year.in_(years)).all():
            bucket = by_period.get((row.year, row.month))
            if bucket is not None:
                bucket.append(row)

    gross, net, tax, overtime, paid = [], [], [], [], []
    for key in keys:
        year, month = (int(p) for p in key.split("-"))
        rows = by_period.get((year, month), [])
        gross.append(round(sum((r.base_salary or 0.0) + (r.allowances or 0.0)
                               + (r.bonuses or 0.0) + (r.arrears or 0.0)
                               + (r.overtime_pay or 0.0) for r in rows), 2))
        net.append(round(sum(r.net_salary or 0.0 for r in rows), 2))
        tax.append(round(sum(r.tax or 0.0 for r in rows), 2))
        overtime.append(round(sum(r.overtime_pay or 0.0 for r in rows), 2))
        paid.append(float(len(rows)))

    # Drop trailing months nobody has been paid for yet. A month with no
    # payroll is not a month the payroll was zero, and plotting it as zero drew
    # a cliff on the chart for the first three weeks of every month.
    pending = bool(paid and paid[-1] == 0)
    while paid and paid[-1] == 0:
        for series in (keys, gross, net, tax, overtime, paid):
            series.pop()

    return PayrollSeries(
        months=keys, gross=gross, net=net, tax=tax, overtime=overtime,
        headcount_paid=paid, currency=settings.CURRENCY,
        current_month_pending=pending,
        tax_percent_of_gross=round(100.0 * sum(tax) / sum(gross), 2) if sum(gross) else None,
        average_cost_per_head=round(gross[-1] / paid[-1], 2)
        if (paid and paid[-1]) else None)


class OvertimeSeries(BaseModel):
    months: List[str]
    approved_hours: List[float]
    approved_pay: List[float]
    by_employee: List[Point]
    currency: str
    # So the frontend can say "no approved overtime in this period" instead of
    # drawing an axis with a flat line along the bottom, which reads as broken.
    has_data: bool = False
    total_hours: float = 0.0
    total_pay: float = 0.0


@router.get("/overtime", response_model=OvertimeSeries)
def overtime_trend(months: int = Query(12, ge=1, le=60),
                   db: Session = Depends(get_db),
                   current_user: User = Depends(require_admin_or_hr)):
    keys = _month_keys(months)
    names = {u.id: u.full_name for u in db.query(User).all()}

    # As with the payroll series: one read for the whole chart rather than one
    # per month, which was thirteen round trips for a twelve-month view.
    wanted = {tuple(int(p) for p in key.split("-")) for key in keys}
    by_period: Dict[tuple, list] = {period: [] for period in wanted}
    if wanted:
        years = {year for year, _ in wanted}
        approved = (db.query(OvertimeRecord)
                    .filter(OvertimeRecord.year.in_(years),
                            OvertimeRecord.status == OvertimeStatusEnum.APPROVED).all())
        for row in approved:
            bucket = by_period.get((row.year, row.month))
            if bucket is not None:
                bucket.append(row)

    hours, pay = [], []
    per_person: Dict[int, float] = {}
    for key in keys:
        year, month = (int(p) for p in key.split("-"))
        rows = by_period.get((year, month), [])
        hours.append(round(sum(r.overtime_hours or 0.0 for r in rows), 2))
        pay.append(round(sum(r.overtime_pay or 0.0 for r in rows), 2))
        for r in rows:
            per_person[r.employee_id] = per_person.get(r.employee_id, 0.0) \
                + float(r.overtime_hours or 0.0)
    return OvertimeSeries(
        months=keys, approved_hours=hours, approved_pay=pay,
        by_employee=[Point(label=names.get(k, f"#{k}"), value=round(v, 1)) for k, v in
                     sorted(per_person.items(), key=lambda i: -i[1])[:10]],
        currency=settings.CURRENCY,
        has_data=any(h > 0 for h in hours),
        total_hours=round(sum(hours), 1), total_pay=round(sum(pay), 2))


# ==========================================================================
# Attendance
# ==========================================================================

class AttendanceSeries(BaseModel):
    months: List[str]
    # Null, not zero, for a month with no records at all. Attendance only goes
    # back as far as the device sync does, and plotting the months before that
    # as a floor of zero reads as "nobody came to work" rather than "we were
    # not collecting this yet".
    attendance_rate_percent: List[Optional[float]]
    average_hours_per_day: List[Optional[float]]
    days_present: List[float]
    incomplete_days: List[float]
    late_arrivals: List[Optional[float]]
    by_employee: List[Dict[str, Any]] = Field(default_factory=list)
    # Null across the board until shifts are assigned: with no stated start
    # time, "late" has no answer, and reporting zero would read as "never late".
    punctuality_available: bool = False
    # The first month there is anything to show, so the chart can say where the
    # record starts instead of implying the earlier months were empty.
    records_begin: Optional[str] = None


@router.get("/attendance", response_model=AttendanceSeries)
def attendance_trend(months: int = Query(6, ge=1, le=24),
                     db: Session = Depends(get_db),
                     current_user: User = Depends(require_admin_or_hr)):
    """
    Who is actually here, and for how long.

    Attendance rate is days present over working days in the month, across
    everyone expected to attend — people marked attendance-exempt are left out
    of both sides rather than dragging the rate down.
    """
    from app.models.attendance import Attendance
    from app.utils.shifts import prime_assignments, punctuality, shift_for

    keys = _month_keys(months)
    expected = [u for u in db.query(User).filter(User.is_active.is_(True)).all()
                if not getattr(u, "attendance_exempt", False)]
    names = {u.id: u.full_name for u in expected}

    # Everybody's shift history in one query rather than one per person.
    # shift_for() is asked about every attendance row below, and it caches per
    # employee — but the first question about each of twenty people was still
    # twenty separate round trips, and against a database this far away that
    # was most of the time this endpoint took.
    prime_assignments(db, list(names) or [0])
    any_shift = any(shift_for(db, u.id) for u in expected)

    # One read for the whole window, bucketed by month here. It used to be a
    # query per month, which is six more round trips for the default range and
    # twenty-four at the maximum, all returning rows from the same table.
    if keys:
        span_start = datetime.combine(_month_bounds(keys[0])[0], datetime.min.time())
        span_end = datetime.combine(_month_bounds(keys[-1])[1], datetime.max.time())
        all_rows = (db.query(Attendance)
                    .filter(Attendance.check_in_time >= span_start,
                            Attendance.check_in_time <= span_end,
                            Attendance.employee_id.in_(list(names) or [0])).all())
    else:
        all_rows = []
    rows_by_month: Dict[str, list] = {}
    for row in all_rows:
        rows_by_month.setdefault(row.check_in_time.strftime("%Y-%m"), []).append(row)

    rates, avg_hours, present_days, incomplete, late = [], [], [], [], []
    per_person: Dict[int, Dict[str, float]] = {
        u.id: {"days": 0.0, "hours": 0.0, "incomplete": 0.0} for u in expected}

    for key in keys:
        first, last = _month_bounds(key)
        working = calculate_working_days(first, min(last, date.today()))

        rows = rows_by_month.get(key, [])
        days = len({(r.employee_id, r.check_in_time.date()) for r in rows})
        hours = sum(r.duration_hours or 0.0 for r in rows)
        open_sessions = sum(1 for r in rows if not r.check_out_time)

        month_late = 0
        for r in rows:
            day = r.check_in_time.date()
            shift = shift_for(db, r.employee_id, day)
            if shift and shift.works_on(day) and punctuality(
                    shift, r.check_in_time, r.check_out_time)["is_late"]:
                month_late += 1
            stats = per_person.get(r.employee_id)
            if stats is not None:
                stats["hours"] += r.duration_hours or 0.0
                if not r.check_out_time:
                    stats["incomplete"] += 1
        for eid, day in {(r.employee_id, r.check_in_time.date()) for r in rows}:
            if eid in per_person:
                per_person[eid]["days"] += 1

        capacity = working * len(expected)
        if not rows:
            rates.append(None)
            avg_hours.append(None)
        else:
            rates.append(round(100.0 * days / capacity, 1) if capacity else None)
            avg_hours.append(round(hours / days, 2) if days else None)
        present_days.append(float(days))
        incomplete.append(float(open_sessions))
        late.append(float(month_late) if any_shift else None)

    by_employee = sorted(
        [{"employee_id": eid, "employee_name": names.get(eid, f"#{eid}"),
          "days_present": v["days"],
          "average_hours": round(v["hours"] / v["days"], 2) if v["days"] else 0.0,
          "incomplete_days": v["incomplete"]}
         for eid, v in per_person.items()],
        key=lambda r: -r["days_present"])

    first_with_data = next((k for k, r in zip(keys, rates) if r is not None), None)
    return AttendanceSeries(
        months=keys, attendance_rate_percent=rates, average_hours_per_day=avg_hours,
        days_present=present_days, incomplete_days=incomplete, late_arrivals=late,
        by_employee=[r for r in by_employee if r["days_present"]],
        punctuality_available=any_shift, records_begin=first_with_data)


# ==========================================================================
# What people are paid
# ==========================================================================

class Compensation(BaseModel):
    monthly_cost: float
    annual_cost: float
    average_salary: float
    median_salary: float
    lowest: float
    highest: float
    # Highest divided by lowest. A number worth knowing before somebody asks.
    spread_ratio: Optional[float] = None
    by_department: List[Dict[str, Any]] = Field(default_factory=list)
    by_designation: List[Dict[str, Any]] = Field(default_factory=list)
    bands: List[Point] = Field(default_factory=list)
    currency: str


@router.get("/compensation", response_model=Compensation)
def compensation(db: Session = Depends(get_db),
                 current_user: User = Depends(require_admin_or_hr)):
    """
    The salary bill as it stands today, from current salaries rather than from
    the last payroll run — so it reflects increments already agreed for a month
    that has not been paid yet.

    Part-time salaries are counted as they are actually paid. Reporting a
    full-time equivalent here would inflate the bill by money nobody receives.
    """
    active = [u for u in db.query(User).filter(User.is_active.is_(True)).all()
              if (u.base_salary or 0) > 0]
    salaries = sorted(float(u.base_salary) for u in active)
    if not salaries:
        return Compensation(monthly_cost=0, annual_cost=0, average_salary=0,
                            median_salary=0, lowest=0, highest=0,
                            currency=settings.CURRENCY)

    total = sum(salaries)
    mid = len(salaries) // 2
    median = (salaries[mid] if len(salaries) % 2
              else (salaries[mid - 1] + salaries[mid]) / 2)

    def group(attr):
        out: Dict[str, List[float]] = {}
        for u in active:
            out.setdefault(getattr(u, attr) or "Unassigned", []).append(float(u.base_salary))
        return sorted(
            [{"label": k, "headcount": len(v), "total": round(sum(v), 2),
              "average": round(sum(v) / len(v), 2),
              "share_percent": round(100.0 * sum(v) / total, 1)}
             for k, v in out.items()], key=lambda r: -r["total"])

    edges = [(0, 50_000), (50_000, 100_000), (100_000, 150_000),
             (150_000, 200_000), (200_000, 10**9)]
    labels = ["Under 50k", "50k – 100k", "100k – 150k", "150k – 200k", "200k +"]
    bands = [Point(label=label, value=sum(1 for x in salaries if lo <= x < hi))
             for (lo, hi), label in zip(edges, labels)]

    return Compensation(
        monthly_cost=round(total, 2), annual_cost=round(total * 12, 2),
        average_salary=round(total / len(salaries), 2), median_salary=round(median, 2),
        lowest=salaries[0], highest=salaries[-1],
        spread_ratio=round(salaries[-1] / salaries[0], 2) if salaries[0] else None,
        by_department=group("department"), by_designation=group("designation"),
        bands=[b for b in bands if b.value], currency=settings.CURRENCY)


# ==========================================================================
# Things somebody needs to do something about
# ==========================================================================

class AttentionItem(BaseModel):
    key: str
    label: str
    count: int
    severity: str          # info | warning | critical
    detail: Optional[str] = None
    people: List[str] = Field(default_factory=list)


@router.get("/attention", response_model=List[AttentionItem])
def needs_attention(db: Session = Depends(get_db),
                    current_user: User = Depends(require_admin_or_hr)):
    """
    The one screen worth opening on a Monday: everything currently waiting on
    somebody, most serious first.

    Deliberately a list of counts with names attached rather than a dashboard
    of gauges — each of these is a thing to go and do, not a trend to watch.
    """
    from app.models.inventory import InvItem
    from app.models.document import DocumentKindEnum, DocumentStatusEnum, EmployeeDocument
    from app.models.leave import Leave, LeaveStatusEnum
    from app.models.lifecycle import OnboardingPacket, TaskStatusEnum
    from app.models.settlement import FinalSettlement, SettlementStatusEnum
    from app.utils.probation import due_for_review

    active = db.query(User).filter(User.is_active.is_(True)).all()
    names = {u.id: u.full_name for u in active}
    items: List[AttentionItem] = []

    def add(key, label, people, severity="warning", detail=None):
        if people:
            items.append(AttentionItem(key=key, label=label, count=len(people),
                                       severity=severity, detail=detail,
                                       people=sorted(people)[:20]))

    # Contracts: the document everything else assumes exists.
    #
    # Two columns, not whole rows. An employee_document carries the full
    # rendered text of the document in body_html — about 33KB for a contract —
    # so loading rows here to read one id off each of them dragged the entire
    # contract archive across the wire. Measured at 1.5s of a 2.8s response,
    # and it grew every time a contract was issued.
    signed = {row[0] for row in db.query(EmployeeDocument.employee_id).filter(
        EmployeeDocument.kind == DocumentKindEnum.CONTRACT,
        EmployeeDocument.status == DocumentStatusEnum.SIGNED).all()}
    add("contract_missing", "No signed contract on file",
        [u.full_name for u in active if u.id not in signed], "critical",
        "Every other document and every review assumes this exists.")

    # Issued and left sitting.
    #
    # VIEWED counts as waiting. Opening a document is not signing it, and
    # excluding the ones people had opened undercounted this by five — the
    # panel said thirteen documents were waiting while twenty people had no
    # signed contract, and the gap had no explanation on screen. Someone who
    # read it and did not sign is exactly who needs chasing.
    stale = db.query(EmployeeDocument.employee_id, EmployeeDocument.title).filter(
        EmployeeDocument.status.in_([DocumentStatusEnum.ISSUED,
                                     DocumentStatusEnum.VIEWED]),
        EmployeeDocument.requires_signature.is_(True)).all()
    add("documents_unsigned", "Documents issued but not signed",
        [f"{names.get(employee_id, employee_id)} — {title}"
         for employee_id, title in stale],
        "warning", "Issued and waiting on the employee.")

    # Probation decisions.
    add("probation_due", "Probation decisions due or overdue",
        [u.full_name for u in due_for_review(db)], "warning",
        "Confirm, extend, or record that it was not confirmed.")

    # Leave awaiting a decision.
    undecided = db.query(Leave).filter(Leave.status == LeaveStatusEnum.PENDING).all()
    add("leave_pending", "Leave requests awaiting a decision",
        [names.get(l.employee_id, str(l.employee_id)) for l in undecided], "warning")

    # Onboarding that stalled.
    open_packets = [p for p in db.query(OnboardingPacket).filter(
        OnboardingPacket.completed_at.is_(None)).all()]
    add("onboarding_open", "Onboarding not finished",
        [names.get(p.employee_id, str(p.employee_id)) for p in open_packets], "info")

    # Settlements blocking an exit.
    drafts = db.query(FinalSettlement).filter(
        FinalSettlement.status == SettlementStatusEnum.DRAFT).all()
    add("settlement_draft", "Final settlements awaiting approval",
        [names.get(s.employee_id, str(s.employee_id)) for s in drafts], "critical",
        "An exit cannot complete until these are approved.")

    # Equipment out with people who have left.
    inactive_ids = {u.id for u in db.query(User).filter(User.is_active.is_(False)).all()}
    stranded = (db.query(InvItem)
                .filter(InvItem.current_holder_id.in_(inactive_ids or {0})).all())
    add("assets_with_leavers", "Equipment still issued to former employees",
        [f"{i.name} ({i.tag})" for i in stranded], "critical")

    # Records HR needs and does not have.
    add("missing_bank_details", "Missing bank details",
        [u.full_name for u in active if not (u.account_number or "").strip()], "info",
        "Needed to pay them.")
    add("missing_cnic", "Missing CNIC number",
        [u.full_name for u in active if not (u.cnic_number or "").strip()], "info",
        "Needed on contracts, payslips and tax certificates.")
    add("missing_joining_date", "No joining date recorded",
        [u.full_name for u in active if not u.date_of_joining], "warning",
        "Drives probation, leave accrual and gratuity.")

    order = {"critical": 0, "warning": 1, "info": 2}
    return sorted(items, key=lambda i: (order[i.severity], -i.count))
