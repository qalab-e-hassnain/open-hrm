"""
What the company owes someone on their last day, and what it takes back.

Every figure here is derived from a record that already exists — the joining
date, the leave balance, the loan schedule, the asset register — so the
settlement is reproducible and each line can be traced to its source. Four of
the lines rest on company policy rather than arithmetic; those read their
answer from settings (see the exit settlement block in app/config.py) and are
documented at the point of use.
"""
import calendar
from datetime import date, datetime, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from app.config import settings
from app.models.leave_balance import LeaveBalance
from app.models.lifecycle import Resignation
from app.models.reimbursement import Reimbursement, ReimbursementStatusEnum
from app.models.settlement import FinalSettlement, SettlementStatusEnum
from app.models.user import User
from app.utils.leave_policy import calculate_working_days
from app.utils.loans import outstanding_for
from app.utils.overtime import WORKING_DAYS_PER_MONTH


def _day_rate(monthly_gross: float) -> float:
    """
    What one working day of this salary is worth.

    Twenty working days to the month, the same figure the overtime and
    unpaid-leave calculations use. Consistency matters more than precision
    here: a leave day encashed at one rate and docked at another would be
    indefensible to the person receiving it.
    """
    basis = (settings.LEAVE_ENCASHMENT_BASIS or "gross").strip().lower()
    # Basic Pay is Gross/1.1 — the split the payslip uses, where the remaining
    # 10% is the tax-exempt medical allowance.
    salary = monthly_gross / 1.1 if basis == "basic" else monthly_gross
    return round(salary / WORKING_DAYS_PER_MONTH, 2)


def years_of_service(joined: Optional[datetime], last_day: date) -> float:
    """
    Completed years, plus one for a remaining part-year over the policy
    threshold — the Ordinance's "any part thereof in excess of six months".
    """
    if not joined:
        return 0.0
    start = joined.date() if isinstance(joined, datetime) else joined
    if last_day <= start:
        return 0.0
    months = (last_day.year - start.year) * 12 + (last_day.month - start.month)
    if last_day.day < start.day:
        months -= 1
    whole_years, remainder_months = divmod(max(0, months), 12)
    if remainder_months > settings.GRATUITY_PART_YEAR_MONTHS:
        whole_years += 1
    return float(whole_years)


def encashable_leave_days(db: Session, employee_id: int, on: date) -> float:
    """
    Unused annual leave for the leave year the last working day falls in.

    Only annual leave is paid out. Sick and casual entitlements are insurance
    against something happening, not deferred pay, so an employee who stayed
    healthy has not accrued a debt the company owes them.
    """
    balance = (db.query(LeaveBalance)
               .filter(LeaveBalance.employee_id == employee_id,
                       LeaveBalance.year == on.year)
               .first())
    if not balance:
        return 0.0
    entitlement = float(balance.annual_total or 0.0)
    if settings.ENCASH_ANNUAL_CARRYOVER:
        entitlement += float(balance.annual_carryover or 0.0)
    return round(max(0.0, entitlement - float(balance.annual_used or 0.0)), 2)


def notice_shortfall_days(resignation: Resignation, last_day: date) -> float:
    """
    Days of notice not served.

    Measured against the notice the resignation itself recorded, not against
    today's policy setting: someone who resigned under a 30-day notice is not
    retrospectively held to a 60-day one. An administrator who agreed an early
    release still sees the shortfall here — waiving it is a decision they make
    on the draft, and one that should be visible rather than silent.
    """
    submitted = resignation.submitted_at
    if isinstance(submitted, datetime):
        # submitted_at is UTC; the last working day is a local calendar date.
        # Subtracting one from the other without converting costs the employee
        # a day of notice whenever they resign between midnight and 5am.
        submitted_date = (submitted + timedelta(
            hours=settings.COMPANY_UTC_OFFSET_HOURS)).date()
    else:
        submitted_date = submitted
    required_end = submitted_date + timedelta(days=int(resignation.notice_days or 0))
    return float(max(0, (required_end - last_day).days))


def _pending_reimbursements(db: Session, employee_id: int) -> float:
    rows = (db.query(Reimbursement)
            .filter(Reimbursement.employee_id == employee_id,
                    Reimbursement.status == ReimbursementStatusEnum.APPROVED,
                    Reimbursement.payroll_id.is_(None))
            .all())
    return round(sum(float(r.amount or 0.0) for r in rows), 2)


def _asset_recovery(db: Session, employee_id: int) -> float:
    """
    Charges already recorded against this employee's equipment.

    Only amounts someone has explicitly entered on a return — nothing is
    charged automatically for kit that is merely still outstanding. Unreturned
    equipment blocks clearance instead, which is a conversation rather than a
    deduction.
    """
    from app.utils.inv_movements import recovery_total_for
    return recovery_total_for(db, employee_id)


def recalculate_totals(s: FinalSettlement) -> FinalSettlement:
    """Add the lines up. Kept separate so a hand-edited draft re-totals too."""
    s.gross_payable = round(
        (s.final_month_amount or 0.0)
        + (s.leave_encashment_amount or 0.0)
        + (s.gratuity_amount or 0.0)
        + (s.pending_reimbursements or 0.0)
        + (s.other_earning or 0.0), 2)
    s.total_deductions = round(
        (s.notice_shortfall_amount or 0.0)
        + (s.loan_outstanding or 0.0)
        + (s.asset_recovery or 0.0)
        + (s.tax_deduction or 0.0)
        + (s.other_deduction or 0.0), 2)
    s.net_payable = round(s.gross_payable - s.total_deductions, 2)
    return s


def compute(db: Session, resignation: Resignation,
            existing: Optional[FinalSettlement] = None,
            actor_id: Optional[int] = None) -> FinalSettlement:
    """
    Build or refresh the draft settlement for a resignation.

    Lines HR has overridden by hand (recorded in `manual_fields`) are left
    exactly as they are, so refreshing a draft to pick up a late expense claim
    never quietly reverses a negotiated figure.
    """
    employee = resignation.employee or db.query(User).filter(
        User.id == resignation.employee_id).first()
    last_day = resignation.agreed_last_working_day or resignation.proposed_last_working_day
    monthly_gross = float((employee.base_salary if employee else 0.0) or 0.0)

    s = existing or FinalSettlement(
        resignation_id=resignation.id, employee_id=resignation.employee_id,
        created_by=actor_id)
    manual = set(s.manual_fields or [])
    s.last_working_day = last_day
    s.computed_at = datetime.utcnow()
    s.monthly_gross = monthly_gross

    day_rate = _day_rate(monthly_gross)

    # Final month, pro-rated over working days — the same basis on which an
    # unpaid day is docked, so leaving mid-month and taking an unpaid day cost
    # the employee the same amount per day.
    month_start = date(last_day.year, last_day.month, 1)
    month_end = date(last_day.year, last_day.month,
                     calendar.monthrange(last_day.year, last_day.month)[1])
    served = calculate_working_days(month_start, last_day)
    total = calculate_working_days(month_start, month_end)
    if "final_month_amount" not in manual:
        s.final_month_days = float(served)
        s.final_month_total_days = float(total)
        s.final_month_amount = round(monthly_gross * (served / total), 2) if total else 0.0

    if "leave_encashment_amount" not in manual:
        days = encashable_leave_days(db, resignation.employee_id, last_day)
        s.leave_encashment_days = days
        s.leave_encashment_amount = round(days * day_rate, 2)

    if "gratuity_amount" not in manual:
        years = years_of_service(employee.date_of_joining if employee else None, last_day)
        s.gratuity_years = years
        if settings.GRATUITY_ENABLED:
            # "Thirty days' wages for every completed year" — thirty days is
            # one month, so the per-year figure is the monthly salary scaled by
            # whatever the policy actually promises.
            per_year = monthly_gross * (settings.GRATUITY_DAYS_PER_YEAR / 30.0)
            s.gratuity_amount = round(per_year * years, 2)
        else:
            s.gratuity_amount = 0.0

    if "pending_reimbursements" not in manual:
        s.pending_reimbursements = _pending_reimbursements(db, resignation.employee_id)

    if "notice_shortfall_amount" not in manual:
        short = notice_shortfall_days(resignation, last_day)
        s.notice_shortfall_days = short
        # Pay in lieu is a calendar-day figure: notice is counted in calendar
        # days, so recovering it at a working-day rate would overcharge by
        # about a third.
        s.notice_shortfall_amount = (
            round((monthly_gross / 30.0) * short, 2)
            if settings.RECOVER_NOTICE_SHORTFALL else 0.0)

    if "loan_outstanding" not in manual:
        s.loan_outstanding = outstanding_for(db, resignation.employee_id)

    if "asset_recovery" not in manual:
        s.asset_recovery = _asset_recovery(db, resignation.employee_id)

    s.other_earning = s.other_earning or 0.0
    s.other_deduction = s.other_deduction or 0.0
    s.tax_deduction = s.tax_deduction or 0.0
    s.manual_fields = sorted(manual)
    return recalculate_totals(s)
