import calendar
import secrets
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from sqlalchemy import or_, func
from typing import List, Optional
from app.database import get_db
from app.config import settings
from app.schemas.payroll import (
    PayrollCreate, PayrollUpdate, PayrollResponse,
    TaxCertificateResponse, TaxCertificateRow, TaxCertificateTotals,
    TaxCertificateCompany, TaxCertificateEmployee,
)
from app.schemas.overtime import OvertimeComputeRequest, OvertimeApproval, OvertimeResponse
from app.models.payroll import Payroll, PayrollStatusEnum, FINAL_STATUSES
from app.models.reimbursement import Reimbursement, ReimbursementStatusEnum
from app.models.overtime import OvertimeRecord, OvertimeStatusEnum
from app.models.attendance import Attendance
from app.models.user import User, RoleEnum
from app.api.deps import get_current_user, require_admin_or_hr, is_privileged
from app.utils.tax import cumulative_monthly_tax, pk_tax_year_position
from app.utils.reimbursements import settle_with_payroll
from app.utils.overtime import (
    calculate_flat_overtime_pay,
    calculate_unpaid_leave_deduction,
    hourly_rate_from_salary,
    hours_by_attendance_id,
    standard_hours_resolver,
)
from app.utils.attendance_session import close_expired_sessions
from app.utils.notifications import queue_payslip_notification
from app.utils import audit
from app.models.leave import Leave
from app.utils.leave_policy import calculate_working_days
from app.utils.loans import release_payroll as release_loan_instalments
from app.utils.loans import sweep_for_payroll as sweep_loan_instalments

router = APIRouter()


def _unpaid_leave_days_in_month(db: Session, employee_id: int, month: int, year: int) -> int:
    """
    Approved UNPAID leave working days that fall inside a given month. Multi-month
    leaves are clipped to the month, and only Mon–Fri days are counted.
    """
    last_day = calendar.monthrange(year, month)[1]
    m_start = datetime(year, month, 1)
    m_end = datetime(year, month, last_day, 23, 59, 59)

    # Fetch and filter status/type in Python — the leave enums are stored with
    # mixed casing, so a DB-side equality filter is unreliable here.
    leaves = db.query(Leave).filter(
        Leave.employee_id == employee_id,
        Leave.start_date <= m_end,
        Leave.end_date >= m_start,
    ).all()

    total = 0
    for l in leaves:
        lt = l.leave_type.value if hasattr(l.leave_type, "value") else str(l.leave_type).split(".")[-1].lower()
        st = l.status.value if hasattr(l.status, "value") else str(l.status).split(".")[-1].lower()
        if lt != "unpaid" or st != "approved":
            continue
        # Clip the leave to this month, then count working days.
        seg_start = max(l.start_date, m_start)
        seg_end = min(l.end_date, m_end)
        total += calculate_working_days(seg_start, seg_end)
    return total


ALLOWANCE_COMPONENTS = (
    "house_allowance", "transport_allowance", "medical_allowance", "other_allowance",
)
DEDUCTION_COMPONENTS = (
    "provident_fund", "social_security", "eobi",
    "loan_repayment", "advance_recovery", "other_deduction",
)


def _sync_payroll_components(payroll: Payroll) -> None:
    """Force the itemised lines to add up to the aggregate totals.

    `allowances` / `deductions` stay authoritative — the payroll sheet, the
    tax calculation and the admin screens all read them — so the itemisation
    is reconciled against them rather than the other way round:

      * items short of the total  -> the remainder lands on the "other" line,
        which is what happens for every legacy row that has only a total;
      * items exceeding the total -> the itemisation is the more specific
        statement, so the total is raised to match it.

    Either way the function leaves sum(items) == total, which is what makes
    the printed payslip reconcile.
    """
    for total_field, components, catch_all in (
        ("allowances", ALLOWANCE_COMPONENTS, "other_allowance"),
        ("deductions", DEDUCTION_COMPONENTS, "other_deduction"),
    ):
        total = round(getattr(payroll, total_field) or 0.0, 2)
        items = round(sum((getattr(payroll, f) or 0.0) for f in components), 2)
        if items == total:
            continue
        if items < total:
            setattr(payroll, catch_all, round((getattr(payroll, catch_all) or 0.0) + (total - items), 2))
        else:
            setattr(payroll, total_field, items)


def payslip_reconciliation(payroll: Payroll) -> dict:
    """Gross - deductions - unpaid-leave - tax, checked against net_salary.

    Returned rather than raised so callers can decide: the read endpoints
    surface it to the UI, while finalisation refuses to proceed on a mismatch.
    """
    gross = round(
        (payroll.base_salary or 0.0)
        + (payroll.allowances or 0.0)
        + (payroll.bonuses or 0.0)
        + (payroll.arrears or 0.0)
        + (payroll.reimbursement_total or 0.0)
        + (payroll.overtime_pay or 0.0),
        2,
    )
    total_deductions = round(
        (payroll.deductions or 0.0)
        + (payroll.unpaid_leave_deduction or 0.0)
        + (payroll.unapproved_leave_fine or 0.0)
        + (payroll.tax or 0.0),
        2,
    )
    expected = round(gross - total_deductions, 2)
    actual = round(payroll.net_salary or 0.0, 2)
    return {
        "gross_earnings": gross,
        "total_deductions": total_deductions,
        "expected_net": expected,
        "net_salary": actual,
        # Tolerance covers float noise only, not a real arithmetic error.
        "balanced": abs(expected - actual) < 0.01,
    }


def _next_payslip_no(db: Session, payroll: Payroll) -> str:
    """Sequential, human-quotable payslip number, e.g. QGT-PS-2026-07-0004."""
    prefix = f"QGT-PS-{payroll.year}-{payroll.month:02d}-"
    # Highest issued, not how many exist. Counting rows re-issues a number the
    # moment one is deleted — with 0001..0016 present, removing 0005 makes the
    # count 15 and the next number 0016, colliding with a payslip already out
    # there. The collision surfaces as an IntegrityError partway through a
    # finalize, after some payslips have been created.
    highest = db.query(func.max(Payroll.payslip_no)).filter(
        Payroll.payslip_no.like(f"{prefix}%")
    ).scalar()
    nxt = (int(highest[len(prefix):]) + 1) if highest else 1
    return f"{prefix}{nxt:04d}"


def _assign_payslip_identity(db: Session, payroll: Payroll) -> None:
    """Stamp number, verification code and generation time at finalisation.

    Deliberately not done for drafts: a draft must not carry a number an
    employee could quote, nor be verifiable.
    """
    if not payroll.payslip_no:
        payroll.payslip_no = _next_payslip_no(db, payroll)
    if not payroll.verification_code:
        # Short, unambiguous, and unguessable enough that a payslip cannot be
        # verified by trying adjacent codes.
        payroll.verification_code = secrets.token_urlsafe(12)
    payroll.generated_at = datetime.utcnow()


def _is_sheet_overtime(payroll) -> bool:
    """
    Whether this payslip's `other_allowance` came from the payroll sheet's
    "Allowance / overtime" column.

    That column mixes two things under one heading, so the note is the only
    signal of which it was. Anything else written there — a genuine allowance
    an admin recorded by hand — is left alone.
    """
    note = (getattr(payroll, "other_allowance_note", None) or "").lower()
    return "payroll sheet" in note and "overtime" in note


def _recompute_payroll(db: Session, payroll: Payroll) -> Payroll:
    """
    Single source of truth for a payslip's numbers. Sweeps in the period's
    admin-approved reimbursements and approved overtime as separate earning
    line items, auto-computes income tax from salary slabs, and recomputes
    Total Earnings / Net Pay. Idempotent — safe to call repeatedly.

    Tax is on SALARY ONLY (base + allowances + bonuses). Reimbursement and
    overtime are added AFTER tax (neither is taxable — per policy).

    Historical (backfilled) rows are an exception: the admin-entered tax is
    trusted as-is, and current reimbursements/overtime are NOT swept in.
    """
    # Advance and loan instalments falling due this month become deduction
    # lines. This runs before the itemisation is reconciled, so the deduction
    # total picks them up rather than disagreeing with the printed lines.
    # Absolute, not additive: re-running it on the same payslip is a no-op.
    if not getattr(payroll, "is_historical", False) and payroll.id is not None:
        due = sweep_loan_instalments(db, payroll)
        payroll.advance_recovery = due["advance_recovery"]
        payroll.advance_recovery_note = due["advance_recovery_note"]
        payroll.loan_repayment = due["loan_repayment"]
        payroll.loan_repayment_note = due["loan_repayment_note"]

    # Reconcile the itemisation first: it can raise a total (see
    # _sync_payroll_components), which must happen before gross is computed.
    _sync_payroll_components(payroll)

    if getattr(payroll, "is_historical", False):
        payroll.reimbursement_total = 0.0
        # Overtime stated on a backfilled row is kept. Sweeping *current*
        # approved overtime into a past period would be wrong, and that is what
        # skipping the query below prevents — but a figure already on the row
        # is a statement of what was actually paid that month, and zeroing it
        # buried the amount inside base salary instead.
        # No unpaid-leave deduction is *derived* for a backfilled row — sweeping
        # today's approved leave into a past period would be wrong. But a figure
        # already on the row is a statement of what actually happened that month,
        # exactly as with overtime above, so it is kept and subtracted. Zeroing it
        # buried a real absence inside a reduced base salary, which made the month
        # look like a pay cut rather than a month somebody was away.
        gross = ((payroll.base_salary or 0.0) + (payroll.allowances or 0.0)
                 + (payroll.bonuses or 0.0) + (payroll.arrears or 0.0)
                 + (payroll.overtime_pay or 0.0))
        payroll.net_salary = round(
            gross
            - (payroll.deductions or 0.0)
            - (payroll.unpaid_leave_deduction or 0.0)
            - (payroll.unapproved_leave_fine or 0.0)
            - (payroll.tax or 0.0),
            2,
        )
        return payroll

    last_day = calendar.monthrange(payroll.year, payroll.month)[1]
    period_start = datetime(payroll.year, payroll.month, 1)
    period_end = datetime(payroll.year, payroll.month, last_day, 23, 59, 59)

    # 1) Approved reimbursements dated in this period, not yet on another payslip.
    reimbursements = db.query(Reimbursement).filter(
        Reimbursement.employee_id == payroll.employee_id,
        Reimbursement.status == ReimbursementStatusEnum.APPROVED,
        Reimbursement.date_of_expense >= period_start,
        Reimbursement.date_of_expense <= period_end,
        or_(Reimbursement.payroll_id.is_(None), Reimbursement.payroll_id == payroll.id),
    ).all()
    reimbursement_total = sum(r.amount for r in reimbursements)
    for r in reimbursements:
        r.payroll_id = payroll.id

    # 2) Approved overtime for this period → flat pay using THIS payroll's salary.
    overtime = db.query(OvertimeRecord).filter(
        OvertimeRecord.employee_id == payroll.employee_id,
        OvertimeRecord.month == payroll.month,
        OvertimeRecord.year == payroll.year,
        OvertimeRecord.status == OvertimeStatusEnum.APPROVED,
    ).first()
    overtime_pay = 0.0
    if overtime:
        if getattr(overtime, "pay_is_manual", False):
            # Manually logged and dually approved — the approved figure is the
            # decision, so recomputing it from salary would overwrite a human
            # judgement with an estimate.
            overtime_pay = round(overtime.overtime_pay or 0.0, 2)
        else:
            employee = db.query(User).filter(User.id == payroll.employee_id).first()
            overtime_pay = calculate_flat_overtime_pay(
                payroll.base_salary, overtime.overtime_hours,
                standard_hours_per_day=employee.standard_hours if employee else None)
            overtime.overtime_pay = overtime_pay
        overtime.payroll_id = payroll.id

        # The payroll sheet has its own "Allowance / overtime" column, so the
        # same overtime can reach a payslip twice: typed there by hand AND
        # swept in from the approved claim. It paid Huzaifa for September
        # twice before anyone noticed. The approved claim wins — it carries
        # two signatures and an audit trail, where the sheet figure carries
        # neither — and the sheet's entry is dropped rather than added on top.
        # Said out loud on the payslip, because money HR typed in is being
        # removed and the note is the only place that shows why.
        sheet_allowance = payroll.other_allowance or 0.0
        if overtime_pay and sheet_allowance and _is_sheet_overtime(payroll):
            payroll.other_allowance = 0.0
            payroll.allowances = round(
                (payroll.allowances or 0.0) - sheet_allowance, 2)
            payroll.other_allowance_note = (
                f"Overtime of Rs {sheet_allowance:,.2f} entered on the payroll "
                f"sheet was removed: the same overtime is already paid from "
                f"the approved time-log claim.")

    # 3) Approved unpaid leave — basic / 160 gives the hourly rate; one working
    #    day is 8 hours. This is salary not earned, not the Rs 5,000 penalty
    #    for an unapproved absence (that is entered manually and separately).
    unpaid_days = _unpaid_leave_days_in_month(db, payroll.employee_id, payroll.month, payroll.year)
    # A day of unpaid leave costs the employee their own day, not the company
    # default: docking a half-day employee eight hours takes twice what they
    # would have earned.
    _emp = db.query(User).filter(User.id == payroll.employee_id).first()
    _day_hours = (_emp.standard_hours if _emp and _emp.standard_hours
                  else settings.STANDARD_WORK_HOURS_PER_DAY)
    unpaid_deduction = calculate_unpaid_leave_deduction(
        payroll.base_salary,
        unpaid_days,
        _day_hours,
        standard_hours_per_day=_day_hours,
    )
    payroll.unpaid_leave_deduction = unpaid_deduction

    # 4) Income tax — company method on the TAXABLE salary (basic only, AFTER
    #    the approved unpaid-leave deduction; allowances/bonuses/reimbursements/overtime/
    #    arrears are all exempt): assume the current salary holds all year
    #    (x12), tax it, subtract the tax already deducted in this tax year's
    #    earlier months, spread the balance over the months remaining.
    taxable_monthly = max(0.0, (payroll.base_salary or 0.0) - unpaid_deduction)
    tax_year = payroll.year + 1 if payroll.month >= 7 else payroll.year
    position = pk_tax_year_position(payroll.month)
    months_remaining = 13 - position  # includes the current month
    # Count EVERY earlier-month payslip of this tax year — draft included. An
    # admin often generates a few months before processing them, and each
    # month's deduction must still build on the previous ones.
    tax_so_far = 0.0
    priors = db.query(Payroll).filter(
        Payroll.employee_id == payroll.employee_id,
    ).all()
    for q in priors:
        if q.id == payroll.id:
            continue
        q_tax_year = q.year + 1 if q.month >= 7 else q.year
        if q_tax_year != tax_year or pk_tax_year_position(q.month) >= position:
            continue
        tax_so_far += (q.tax or 0.0)
    tax = cumulative_monthly_tax(tax_so_far, taxable_monthly, months_remaining)

    payroll.reimbursement_total = round(reimbursement_total, 2)
    payroll.overtime_pay = round(overtime_pay, 2)
    # A manually-set figure wins over the slab projection. The slab engine sees
    # only gross salary, so it cannot account for pension-scheme contributions
    # that are exempt from salary tax — recomputing would silently overwrite
    # the correct, lower number with a wrong, higher one.
    if not getattr(payroll, "tax_is_manual", False):
        payroll.tax = round(tax, 2)

    gross_earnings = (
        (payroll.base_salary or 0.0)
        + (payroll.allowances or 0.0)
        + (payroll.bonuses or 0.0)
        + (payroll.arrears or 0.0)
        + payroll.reimbursement_total
        + payroll.overtime_pay
    )
    payroll.net_salary = round(
        gross_earnings
        - (payroll.deductions or 0.0)
        - payroll.unpaid_leave_deduction
        - (payroll.unapproved_leave_fine or 0.0)
        - payroll.tax,
        2,
    )
    return payroll


@router.get("/", response_model=List[PayrollResponse])
def list_payrolls(
    response: Response,
    skip: int = 0,
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin_or_hr)
):
    """
    Payroll records, newest period first.

    Two things were wrong here and they compounded each other. There was no
    ORDER BY, so which records came back was whatever order the database
    happened to return; and the page size was capped at 100 with no way for
    the caller to know it had been capped. With 168 records on file, 68 of
    them simply did not exist as far as the portal was concerned, and nothing
    said so.

    The order is now fixed, and X-Total-Count says how many there are, so a
    caller can tell a full page from the end of the list and ask for the rest.
    """
    total = db.query(func.count(Payroll.id)).scalar() or 0
    response.headers["X-Total-Count"] = str(total)
    # Exposed through CORS explicitly: the portal is on another origin and
    # cannot read a response header it has not been granted.
    response.headers["Access-Control-Expose-Headers"] = "X-Total-Count"
    payrolls = (
        db.query(Payroll)
        .order_by(Payroll.year.desc(), Payroll.month.desc(), Payroll.id.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )
    return payrolls


@router.get("/payslips", response_model=List[PayrollResponse])
def get_payslips(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Get payslips (payroll records) for current user - alias for /me"""
    payrolls = db.query(Payroll).filter(Payroll.employee_id == current_user.id).all()
    return payrolls


@router.get("/me", response_model=List[PayrollResponse])
def get_my_payrolls(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Get payroll records for current user"""
    payrolls = db.query(Payroll).filter(Payroll.employee_id == current_user.id).all()
    return payrolls


@router.get("/employee/{employee_id}", response_model=List[PayrollResponse])
def get_employee_payrolls(
    employee_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin_or_hr)
):
    """Get payroll records for a specific employee (Admin only)"""
    payrolls = db.query(Payroll).filter(Payroll.employee_id == employee_id).all()
    return payrolls


# ----------------------------- Tax Certificate -----------------------------

def _default_range() -> tuple:
    """
    Sensible default window when the caller doesn't pass one: the current
    Pakistan tax year to date (July of the tax year's start .. current month).
    """
    now = datetime.utcnow()
    start_year = now.year if now.month >= 7 else now.year - 1
    return (start_year, 7, now.year, now.month)


def _resolve_range(start_year, start_month, end_year, end_month) -> tuple:
    """Fill in any missing bound from the default, then validate."""
    d_sy, d_sm, d_ey, d_em = _default_range()
    sy = start_year or d_sy
    sm = start_month or d_sm
    ey = end_year or d_ey
    em = end_month or d_em
    for m in (sm, em):
        if not 1 <= m <= 12:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                                detail="Month must be between 1 and 12.")
    if (sy * 12 + sm) > (ey * 12 + em):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Start month must not be after end month.")
    return (sy, sm, ey, em)


def _cumulative_tax_map(payrolls) -> dict:
    """
    {payroll_id: monthly tax} using the cumulative (projected-annual) method,
    computed per PK tax year across ALL of an employee's finalized payrolls in
    month order. Historical rows keep their admin-entered tax but still count
    toward the running actuals for later months.
    """
    by_year: dict = {}
    for p in payrolls:
        ty = p.year + 1 if p.month >= 7 else p.year
        by_year.setdefault(ty, []).append(p)

    out: dict = {}
    for rows in by_year.values():
        rows.sort(key=lambda p: pk_tax_year_position(p.month))
        tax_so_far = 0.0
        for p in rows:
            # Taxable basic is reduced by any approved unpaid-leave deduction.
            taxable = max(0.0, (p.base_salary or 0.0) - (p.unpaid_leave_deduction or 0.0))
            if getattr(p, "is_historical", False):
                tax = p.tax or 0.0
            else:
                months_remaining = 13 - pk_tax_year_position(p.month)
                tax = cumulative_monthly_tax(tax_so_far, taxable, months_remaining)
            out[p.id] = tax
            tax_so_far += tax
    return out


def _build_tax_certificate(db: Session, employee: User,
                           start_year: int, start_month: int,
                           end_year: int, end_month: int) -> TaxCertificateResponse:
    """
    Aggregate an employee's PROCESSED/PAID payrolls between two months
    (inclusive) into a tax statement for that period.

    Total Income = basic + allowances + bonuses + reimbursements + arrears.
    Exempt = everything except basic. Taxable = basic salary. Tax Deducted is
    the cumulative (projected-annual) monthly withholding — recomputed live so
    every generate reflects the latest logic and any increments.
    """
    start_ord = start_year * 12 + start_month
    end_ord = end_year * 12 + end_month

    # The cumulative method needs the WHOLE tax year, so pull all the employee's
    # finalized payrolls, compute each month's deduction, then display only the
    # requested range. (year*12 + month) is a single sortable month ordinal.
    all_payrolls = db.query(Payroll).filter(
        Payroll.employee_id == employee.id,
        Payroll.status.in_([PayrollStatusEnum.PROCESSED, PayrollStatusEnum.PAID]),
    ).all()
    tax_map = _cumulative_tax_map(all_payrolls)

    payrolls = [p for p in all_payrolls if start_ord <= p.year * 12 + p.month <= end_ord]
    payrolls.sort(key=lambda p: (p.year, p.month))

    rows: List[TaxCertificateRow] = []
    cumulative_tax = 0.0
    tot_income = tot_exempt = tot_taxable = tot_tax = 0.0

    for p in payrolls:
        # Exempt = allowances + bonuses + reimbursements + arrears.
        # Taxable = base (basic) salary. Tax is charged on the taxable part only.
        exempt = ((p.allowances or 0.0) + (p.bonuses or 0.0)
                  + (p.reimbursement_total or 0.0) + (p.arrears or 0.0))
        taxable = (p.base_salary or 0.0)
        total_income = taxable + exempt
        tax_paid = tax_map.get(p.id, 0.0)
        cumulative_tax += tax_paid

        rows.append(TaxCertificateRow(
            month=p.month,
            year=p.year,
            month_label=f"{calendar.month_abbr[p.month]} {p.year}",
            total_income=round(total_income, 2),
            exempt_income=round(exempt, 2),
            taxable_income=round(taxable, 2),
            tax_paid=round(tax_paid, 2),
            cumulative_tax=round(cumulative_tax, 2),
        ))
        tot_income += total_income
        tot_exempt += exempt
        tot_taxable += taxable
        tot_tax += tax_paid

    period_label = (
        f"{calendar.month_abbr[start_month]} {start_year} - "
        f"{calendar.month_abbr[end_month]} {end_year}"
    )

    return TaxCertificateResponse(
        company=TaxCertificateCompany(
            name=settings.COMPANY_NAME,
            reg_no=settings.COMPANY_REG_NO,
            ntn=settings.COMPANY_REG_NO,
            address=settings.COMPANY_ADDRESS,
            email=settings.COMPANY_EMAIL,
        ),
        employee=TaxCertificateEmployee(
            name=employee.full_name,
            cnic=employee.cnic_number,
            employee_id=employee.employee_id,
            designation=employee.designation,
        ),
        period_label=period_label,
        currency=settings.CURRENCY,
        rows=rows,
        totals=TaxCertificateTotals(
            total_income=round(tot_income, 2),
            exempt_income=round(tot_exempt, 2),
            taxable_income=round(tot_taxable, 2),
            tax_paid=round(tot_tax, 2),
        ),
    )


@router.get("/tax-certificate/me", response_model=TaxCertificateResponse)
def get_my_tax_certificate(
    start_year: Optional[int] = Query(None),
    start_month: Optional[int] = Query(None, ge=1, le=12),
    end_year: Optional[int] = Query(None),
    end_month: Optional[int] = Query(None, ge=1, le=12),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Tax certificate for the logged-in user over an optional month range."""
    sy, sm, ey, em = _resolve_range(start_year, start_month, end_year, end_month)
    return _build_tax_certificate(db, current_user, sy, sm, ey, em)


@router.get("/tax-certificate/{employee_id:int}", response_model=TaxCertificateResponse)
def get_employee_tax_certificate(
    employee_id: int,
    start_year: Optional[int] = Query(None),
    start_month: Optional[int] = Query(None, ge=1, le=12),
    end_year: Optional[int] = Query(None),
    end_month: Optional[int] = Query(None, ge=1, le=12),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin_or_hr),
):
    """Tax certificate for a specific employee over a month range (Admin only)."""
    employee = db.query(User).filter(User.id == employee_id).first()
    if not employee:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Employee not found")
    sy, sm, ey, em = _resolve_range(start_year, start_month, end_year, end_month)
    return _build_tax_certificate(db, employee, sy, sm, ey, em)


@router.get("/{payroll_id:int}", response_model=PayrollResponse)
def get_payroll(
    payroll_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Get payroll by ID"""
    payroll = db.query(Payroll).filter(Payroll.id == payroll_id).first()
    
    if not payroll:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Payroll not found"
        )
    
    # Users can see their own payroll, admin can see all
    if not is_privileged(current_user) and payroll.employee_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not enough permissions"
        )
    
    return payroll


@router.post("/", response_model=PayrollResponse, status_code=status.HTTP_201_CREATED)
def create_payroll(
    payroll_data: PayrollCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin_or_hr)
):
    """Create a new payroll record (Admin only)"""
    # Check if employee exists
    employee = db.query(User).filter(User.id == payroll_data.employee_id).first()
    if not employee:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Employee not found"
        )
    
    # Check if payroll already exists for this month/year
    existing = db.query(Payroll).filter(
        Payroll.employee_id == payroll_data.employee_id,
        Payroll.month == payroll_data.month,
        Payroll.year == payroll_data.year
    ).first()
    
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Payroll for this month/year already exists"
        )
    
    payroll = Payroll(
        employee_id=payroll_data.employee_id,
        base_salary=payroll_data.base_salary,
        allowances=payroll_data.allowances,
        bonuses=payroll_data.bonuses,
        arrears=payroll_data.arrears,
        deductions=payroll_data.deductions,
        unapproved_leave_fine=payroll_data.unapproved_leave_fine,
        # For a normal row, tax + net_salary are computed by _recompute_payroll
        # (seed 0 then overwrite). For a historical row, the admin-entered tax
        # is trusted, so seed it here.
        tax=payroll_data.tax if payroll_data.is_historical else 0.0,
        net_salary=0.0,
        month=payroll_data.month,
        year=payroll_data.year,
        notes=payroll_data.notes,
        is_historical=payroll_data.is_historical,
        status=PayrollStatusEnum.DRAFT
    )

    db.add(payroll)
    db.flush()  # assign payroll.id so approved items can be stamped to it
    _recompute_payroll(db, payroll)

    # Generating a payslip issues it immediately: employees cannot see drafts.
    # Finalize and queue their alert in this same database transaction.
    figures = payslip_reconciliation(payroll)
    if not figures["balanced"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The generated payslip figures do not reconcile.",
        )
    _assign_payslip_identity(db, payroll)
    payroll.status = PayrollStatusEnum.PROCESSED
    queue_payslip_notification(
        db,
        user_id=payroll.employee_id,
        payroll_id=payroll.id,
        month=payroll.month,
        year=payroll.year,
    )

    # Increment: also save this pay as the employee's new standing salary, so
    # subsequent payslips prefill it and the cumulative tax re-projects onward.
    if payroll_data.apply_increment:
        employee.base_salary = payroll_data.base_salary
        employee.allowance = payroll_data.allowances

    db.commit()
    db.refresh(payroll)

    return payroll


@router.put("/{payroll_id:int}", response_model=PayrollResponse)
def update_payroll(
    payroll_id: int,
    payroll_data: PayrollUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin_or_hr)
):
    """Update payroll record (Admin only)"""
    payroll = db.query(Payroll).filter(Payroll.id == payroll_id).first()
    
    if not payroll:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Payroll not found"
        )
    
    # A payslip stops being editable once it has been issued, not once it has
    # been paid. Processed means it has gone out: somebody has seen the figure,
    # and silently rewriting it leaves no trace that it ever said anything
    # else. Corrections go through POST /payslips/{id}/correct, which supersedes
    # the original with a new revision and a stated reason, and leaves both on
    # the record — which is what an audit needs and an edit destroys.
    if payroll.status in FINAL_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(f"This payslip is {payroll.status.value} and cannot be "
                    f"edited. Issue a correction instead "
                    f"(POST /payslips/{payroll.id}/correct), which records a "
                    f"reason and keeps the original."),
        )

    update_data = payroll_data.model_dump(exclude_unset=True)
    # Tax is editable on any payslip, not just a backfilled one: exemptions the
    # slab engine cannot see (pension-scheme contributions) mean the correct
    # figure is often not the computed one. Supplying it pins the value so the
    # next recompute preserves it; `tax_is_manual: false` hands it back to the
    # slab engine.
    if "tax" in update_data:
        payroll.tax_is_manual = True
    if update_data.get("tax_is_manual") is False:
        payroll.tax_is_manual = False
        update_data.pop("tax", None)
    for field, value in update_data.items():
        setattr(payroll, field, value)

    # Recompute tax, swept-in earnings, and net pay from scratch.
    _recompute_payroll(db, payroll)

    db.commit()
    db.refresh(payroll)

    return payroll


@router.put("/{payroll_id:int}/process", response_model=PayrollResponse)
def process_payroll(
    payroll_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin_or_hr)
):
    """Mark payroll as processed (Admin only)"""
    payroll = db.query(Payroll).filter(Payroll.id == payroll_id).first()
    
    if not payroll:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Payroll not found"
        )
    
    if payroll.status == PayrollStatusEnum.PAID:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Payroll is already paid"
        )

    was_final = payroll.status in (PayrollStatusEnum.PROCESSED, PayrollStatusEnum.PAID)
    _assign_payslip_identity(db, payroll)
    payroll.status = PayrollStatusEnum.PROCESSED
    if not was_final:
        queue_payslip_notification(
            db,
            user_id=payroll.employee_id,
            payroll_id=payroll.id,
            month=payroll.month,
            year=payroll.year,
        )
    
    db.commit()
    db.refresh(payroll)
    
    return payroll


class MarkPaidRequest(BaseModel):
    """
    When the money actually moved, and how it was sent.

    `payment_date` is optional and defaults to now, which is right for a payment
    being recorded as it happens. It exists because that default is wrong for
    every other case: recording a back-dated payslip stamps the day somebody
    pressed the button, so a payslip for July can end up claiming it was paid in
    September. Leave it null only when the real date is genuinely unknown.
    """
    payment_date: Optional[datetime] = None
    payment_method: Optional[str] = Field(None, max_length=80)
    payment_reference: Optional[str] = Field(None, max_length=120)


class BulkMarkPaidRequest(MarkPaidRequest):
    # Settling a whole month with no date given would stamp every payslip with
    # the moment the button was pressed — which is how four July payslips came
    # to record a September payment date. So bulk settlement will not guess:
    # give the date, or say explicitly that it is not known.
    payment_date_unknown: bool = False
    # Either a specific set of payslips, or every unpaid one in a period.
    payroll_ids: Optional[List[int]] = None
    month: Optional[int] = Field(None, ge=1, le=12)
    year: Optional[int] = Field(None, ge=2000, le=2100)
    # Guard against a mistyped filter silently settling the wrong month: the
    # caller states how many payslips they expect and the request is refused if
    # the count disagrees.
    expected_count: Optional[int] = Field(None, ge=0)


def _settle(db: Session, payroll: Payroll, payload: "BulkMarkPaidRequest") -> None:
    """
    Mark one payslip paid. Shared by the single and bulk endpoints.

    A payslip settled with no known date keeps a null payment_date rather than
    borrowing today's. "Paid, date not recorded" is true; "paid on 6 September"
    on a payslip for July 2025 is not, and it is the version an employee hands
    to their bank.
    """
    was_final = payroll.status in (PayrollStatusEnum.PROCESSED, PayrollStatusEnum.PAID)
    _assign_payslip_identity(db, payroll)
    payroll.status = PayrollStatusEnum.PAID
    if payload.payment_date is not None:
        payroll.payment_date = payload.payment_date
    if payload.payment_method:
        payroll.payment_method = payload.payment_method
    if payload.payment_reference:
        payroll.payment_reference = payload.payment_reference
    # A reimbursement this payslip absorbed is paid the moment the payslip is.
    # Left at "approved" it keeps showing up as money still owed, which is how
    # somebody ends up handing over cash for it a second time.
    settle_with_payroll(db, payroll)
    if not was_final:
        queue_payslip_notification(
            db, user_id=payroll.employee_id, payroll_id=payroll.id,
            month=payroll.month, year=payroll.year)


@router.post("/mark-paid")
def bulk_mark_paid(
    payload: BulkMarkPaidRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin_or_hr),
):
    """
    Settle a whole month at once, rather than one payslip at a time.

    Payroll is paid in a single run, so recording it should be one action too.
    Pass `month` and `year` to settle everything outstanding for a period, or
    `payroll_ids` for a specific set. Anything already paid is left alone and
    reported back, so this is safe to repeat.
    """
    q = db.query(Payroll).filter(Payroll.status != PayrollStatusEnum.PAID)
    if payload.payroll_ids:
        q = q.filter(Payroll.id.in_(payload.payroll_ids))
    if payload.month is not None:
        q = q.filter(Payroll.month == payload.month)
    if payload.year is not None:
        q = q.filter(Payroll.year == payload.year)
    if not (payload.payroll_ids or payload.month is not None or payload.year is not None):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Say which payslips to settle: payroll_ids, or a month and year.")
    if payload.payment_date is None and not payload.payment_date_unknown:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Give the date the payment was made. If it genuinely is not "
                   "known — settling historical payslips, say — send "
                   "payment_date_unknown: true and the date is left blank rather "
                   "than filled in with today's.")

    # Cancelled and corrected payslips are not awaiting payment.
    targets = [p for p in q.all() if p.status in
               (PayrollStatusEnum.DRAFT, PayrollStatusEnum.PROCESSING,
                PayrollStatusEnum.PROCESSED)]

    if payload.expected_count is not None and len(targets) != payload.expected_count:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"This would settle {len(targets)} payslips, not the "
                   f"{payload.expected_count} you expected. Nothing was changed.")

    for payroll in targets:
        _settle(db, payroll, payload)
    db.commit()

    total = round(sum(p.net_salary or 0.0 for p in targets), 2)
    periods = sorted({f"{p.year}-{p.month:02d}" for p in targets})
    audit.record(db, "payroll.bulk_marked_paid", actor=current_user, request=request,
                 target_type="payroll",
                 detail={"count": len(targets), "net_total": total,
                         "periods": periods,
                         "payment_date": str(payload.payment_date or "now")})
    return {"marked_paid": len(targets), "net_total": total, "periods": periods,
            "payment_date_recorded": payload.payment_date is not None,
            "currency": settings.CURRENCY}


@router.put("/{payroll_id:int}/paid", response_model=PayrollResponse)
def mark_payroll_paid(
    payroll_id: int,
    payload: Optional[MarkPaidRequest] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin_or_hr)
):
    """Mark one payslip as paid."""
    payroll = db.query(Payroll).filter(Payroll.id == payroll_id).first()
    
    if not payroll:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Payroll not found"
        )
    
    was_final = payroll.status in (PayrollStatusEnum.PROCESSED, PayrollStatusEnum.PAID)
    _assign_payslip_identity(db, payroll)
    payroll.status = PayrollStatusEnum.PAID
    payroll.payment_date = (payload.payment_date if payload and payload.payment_date
                            else datetime.utcnow())
    if payload and payload.payment_method:
        payroll.payment_method = payload.payment_method
    if payload and payload.payment_reference:
        payroll.payment_reference = payload.payment_reference
    # See the note in _settle(): the claims this payslip carried are paid too.
    settle_with_payroll(db, payroll)
    if not was_final:
        queue_payslip_notification(
            db,
            user_id=payroll.employee_id,
            payroll_id=payroll.id,
            month=payroll.month,
            year=payroll.year,
        )
    
    db.commit()
    db.refresh(payroll)
    
    return payroll


@router.delete("/{payroll_id:int}", status_code=status.HTTP_204_NO_CONTENT)
def delete_payroll(
    payroll_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin_or_hr)
):
    """Delete payroll record (Admin only)"""
    payroll = db.query(Payroll).filter(Payroll.id == payroll_id).first()
    
    if not payroll:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Payroll not found"
        )
    
    # A draft can be thrown away. Anything that has been issued cannot: a
    # deleted payslip leaves nothing behind to show it ever existed, and the
    # reimbursements, overtime and loan instalments it carried are released
    # back as though they were never paid. That is a hole an auditor would put
    # their finger on straight away, and it was open at every status including
    # paid.
    if payroll.status in FINAL_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(f"This payslip is {payroll.status.value} and cannot be "
                    f"deleted. Cancel or correct it instead, so the record "
                    f"shows what happened."),
        )

    # Release any reimbursements/overtime stamped to it so a regenerated
    # payslip for the same period sweeps them in again.
    db.query(Reimbursement).filter(Reimbursement.payroll_id == payroll.id).update({"payroll_id": None})
    db.query(OvertimeRecord).filter(OvertimeRecord.payroll_id == payroll.id).update({"payroll_id": None})
    # Same for any advance/loan instalment this payslip recovered — otherwise
    # deleting a draft quietly forgives the deduction it was carrying.
    release_loan_instalments(db, payroll.id)
    # A corrected payslip's newer revision points back at this one via
    # supersedes_id (see Payroll.supersedes_id). Deleting a superseded/original
    # row without releasing that link first violates its foreign key —
    # exactly what "Something went wrong on our end" was, on any record that
    # had ever been corrected. The newer revision stands fine on its own; it
    # just loses the "this replaced X" breadcrumb once X is gone.
    db.query(Payroll).filter(Payroll.supersedes_id == payroll.id).update({"supersedes_id": None})
    db.delete(payroll)
    db.commit()

    return None


@router.post("/{payroll_id:int}/recompute", response_model=PayrollResponse)
def recompute_payroll(
    payroll_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin_or_hr),
):
    """
    Re-sweep the period's approved reimbursements + approved overtime into this
    payslip and recompute tax/net (Admin only). Use after approving items.
    """
    payroll = db.query(Payroll).filter(Payroll.id == payroll_id).first()
    if not payroll:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Payroll not found")
    # Recompute is allowed at ANY status (including paid) so an admin can
    # regenerate a payslip's figures even after submission.
    _recompute_payroll(db, payroll)
    db.commit()
    db.refresh(payroll)
    return payroll


# ---------------------------------------------------------------------------
# Overtime approval (admin gate for Feature 2)
# ---------------------------------------------------------------------------

def _overtime_to_response(record: OvertimeRecord) -> OvertimeResponse:
    resp = OvertimeResponse.model_validate(record)
    if record.employee:
        resp.employee_name = record.employee.full_name
        # What these hours are worth at the employee's current salary, so the
        # amount is on screen before the approval rather than after it. The paid
        # figure is recomputed from the payroll's own base salary on approval.
        resp.hourly_rate = hourly_rate_from_salary(
            record.employee.base_salary,
            standard_hours_per_day=record.employee.standard_hours)
        resp.estimated_pay = calculate_flat_overtime_pay(
            record.employee.base_salary, record.overtime_hours,
            standard_hours_per_day=record.employee.standard_hours)
    return resp


def _sync_period_payroll(db: Session, employee_id: int, month: int, year: int):
    """If a (non-paid) payroll already exists for this period, recompute it so
    a freshly-approved overtime/reimbursement shows up immediately."""
    payroll = db.query(Payroll).filter(
        Payroll.employee_id == employee_id,
        Payroll.month == month,
        Payroll.year == year,
    ).first()
    if payroll and payroll.status != PayrollStatusEnum.PAID:
        _recompute_payroll(db, payroll)


# POST /overtime/compute is gone. It derived overtime from attendance —
# every hour past the eighth became a claim nobody made — and wrote records
# that bypassed the two signatures overtime now requires. Overtime is
# claimed per date worked through /api/v1/time-logs and approved by the line
# manager and HR; a record can no longer appear against someone without
# both. Hours beyond the standard day are still visible on attendance as a
# fact about the day; they are not an entitlement.


@router.get("/overtime", response_model=List[OvertimeResponse])
def list_overtime(
    month: Optional[int] = None,
    year: Optional[int] = None,
    status_filter: Optional[OvertimeStatusEnum] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin_or_hr),
):
    """List overtime records (Admin only)."""
    query = db.query(OvertimeRecord)
    if month is not None:
        query = query.filter(OvertimeRecord.month == month)
    if year is not None:
        query = query.filter(OvertimeRecord.year == year)
    if status_filter is not None:
        query = query.filter(OvertimeRecord.status == status_filter)
    records = query.order_by(OvertimeRecord.year.desc(), OvertimeRecord.month.desc()).all()
    return [_overtime_to_response(r) for r in records]


@router.get("/overtime/me", response_model=List[OvertimeResponse])
def get_my_overtime(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """An employee's own overtime records."""
    records = db.query(OvertimeRecord).filter(
        OvertimeRecord.employee_id == current_user.id
    ).order_by(OvertimeRecord.year.desc(), OvertimeRecord.month.desc()).all()
    return [_overtime_to_response(r) for r in records]


@router.put("/overtime/{record_id}/approve", response_model=OvertimeResponse)
def approve_overtime(
    record_id: int,
    approval: OvertimeApproval,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin_or_hr),
):
    """Approve or reject an overtime record (Admin only)."""
    if approval.status not in (OvertimeStatusEnum.APPROVED, OvertimeStatusEnum.REJECTED):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="status must be 'approved' or 'rejected'"
        )
    record = db.query(OvertimeRecord).filter(OvertimeRecord.id == record_id).first()
    if not record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Overtime record not found")
    if record.status != OvertimeStatusEnum.PENDING:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This overtime record has already been processed"
        )

    record.status = approval.status
    record.approved_by = current_user.id
    record.approved_at = datetime.utcnow()

    # If a payroll already exists for the period, reflect the decision now.
    # Flush first so the recompute's "status == APPROVED" query sees this change.
    if approval.status == OvertimeStatusEnum.APPROVED:
        db.flush()
        _sync_period_payroll(db, record.employee_id, record.month, record.year)

    db.commit()
    db.refresh(record)
    return _overtime_to_response(record)
