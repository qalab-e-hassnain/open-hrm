"""Payslip document API.

payroll.py owns the payroll *record* an admin edits. This module owns what the
employee receives: an assembled document with named lines, totals that
reconcile, an identity that can be verified, and the dispute thread attached
to it. Building the document server-side keeps a single definition of a
payslip rather than one per client.
"""
import calendar
import logging
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.database import get_db
from app.config import settings
from app.api.deps import get_current_user, require_admin_or_hr, is_privileged
from app.models.payroll import Payroll, PayrollStatusEnum, FINAL_STATUSES
from app.models.overtime import OvertimeRecord, OvertimeStatusEnum
from app.utils.overtime import hourly_rate_from_salary
from app.models.payslip_dispute import PayslipDispute, DisputeStatusEnum
from app.models.user import User, RoleEnum
from app.models.notification import NotificationTypeEnum, RelatedTypeEnum
from app.schemas.payslip import (
    PayslipDetailResponse, PayslipSummary, PayslipYTD, PayslipLine,
    PayslipCompany, PayslipEmployee, PayslipReconciliation, PayslipRevision,
    PayslipVerificationResponse, DisputeCreate, DisputeRespond, DisputeResponse,
    PayslipCorrectionRequest, PayslipCancellationRequest,
)
from app.api.v1.payroll import payslip_reconciliation, _assign_payslip_identity
from app.utils.email import send_payslip_email, send_payslip_dispute_email
from app.utils.notifications import queue_payslip_notification, notify_admins, create_notification

router = APIRouter()

MONTH_ABBR = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun",
              "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

# Mirrors frontend/src/services/payslipService.ts's DISPUTE_CATEGORIES labels
# — used for the admin-facing notification/email text, where the raw enum
# value ("incorrect_salary") would read as a bug rather than a category.
DISPUTE_CATEGORY_LABELS = {
    "incorrect_salary": "Incorrect salary",
    "missing_allowance": "Missing allowance",
    "unexpected_deduction": "Unexpected deduction",
    "tax_issue": "Tax issue",
    "bank_payment_issue": "Bank payment issue",
    "other": "Other",
}

# A payslip an employee is allowed to open at all. Drafts are deliberately
# excluded: an unfinalised figure must never be mistaken for what will be paid.
EMPLOYEE_VISIBLE = (
    PayrollStatusEnum.PROCESSING, PayrollStatusEnum.PROCESSED,
    PayrollStatusEnum.PAID, PayrollStatusEnum.CORRECTED,
    PayrollStatusEnum.CANCELLED,
)


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _is_privileged(user: User) -> bool:
    """Admins and HR managers both see payroll in full."""
    return is_privileged(user)


def _mask_account(account_number: Optional[str]) -> Optional[str]:
    """Last four digits only — the full number never leaves the server."""
    if not account_number:
        return None
    digits = "".join(ch for ch in str(account_number) if ch.isalnum())
    if len(digits) <= 4:
        return f"•••• {digits}"
    return f"•••• {digits[-4:]}"


def _period_bounds(payroll: Payroll):
    last_day = calendar.monthrange(payroll.year, payroll.month)[1]
    return (
        datetime(payroll.year, payroll.month, 1),
        datetime(payroll.year, payroll.month, last_day, 23, 59, 59),
    )


def _period_label(payroll: Payroll) -> str:
    start, end = _period_bounds(payroll)
    return f"{start:%d %b %Y} - {end:%d %b %Y}"


def _resolve_target_employee(
    db: Session, current_user: User, employee_id: Optional[int]
) -> int:
    """Employees are pinned to themselves; only an admin may name someone else."""
    if employee_id is None or employee_id == current_user.id:
        return current_user.id
    if not _is_privileged(current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only view your own payslips.",
        )
    if not db.query(User).filter(User.id == employee_id).first():
        raise HTTPException(status_code=404, detail="Employee not found.")
    return employee_id


def _visible_payslips(db: Session, employee_id: int, viewer: User):
    q = db.query(Payroll).filter(Payroll.employee_id == employee_id)
    if not _is_privileged(viewer):
        q = q.filter(Payroll.status.in_(EMPLOYEE_VISIBLE))
    return q


def _load_payslip(db: Session, payroll_id: int, viewer: User) -> Payroll:
    payroll = db.query(Payroll).filter(Payroll.id == payroll_id).first()
    if not payroll:
        raise HTTPException(status_code=404, detail="Payslip not found.")
    if payroll.employee_id != viewer.id and not _is_privileged(viewer):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only view your own payslips.",
        )
    if payroll.status == PayrollStatusEnum.DRAFT and not _is_privileged(viewer):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This payslip has not been finalised yet.",
        )
    return payroll


def _overtime_formula(db: Session, payroll: Payroll) -> Optional[str]:
    """Explain overtime pay as hours x rate, using the approved record."""
    if not (payroll.overtime_pay or 0):
        return None
    record = db.query(OvertimeRecord).filter(
        OvertimeRecord.employee_id == payroll.employee_id,
        OvertimeRecord.month == payroll.month,
        OvertimeRecord.year == payroll.year,
        OvertimeRecord.status == OvertimeStatusEnum.APPROVED,
    ).first()
    if not record or not record.overtime_hours:
        return None
    hours = int(record.overtime_hours)
    minutes = int(round((record.overtime_hours - hours) * 60))
    # Show the arithmetic: the rate is this payslip's basic spread over a
    # 160-hour month, so hours x rate reconciles with the amount on the line.
    rate = hourly_rate_from_salary(payroll.base_salary)
    return f"{hours}h {minutes:02d}m of approved overtime x Rs {rate:,.2f}/hour (basic / 160)"


# Every standard component appears on every payslip, including the ones that
# came to nothing this month — a line reading 0.00 states "this was considered
# and it was nil", whereas an absent line leaves the reader guessing whether it
# was zero or simply not handled. Only the two free-text "other" lines carry a
# custom label, and they fall back to a generic one when unnamed.
def _build_earnings(db: Session, payroll: Payroll) -> List[PayslipLine]:
    """Named earning lines. Only basic pay is taxable — see _recompute_payroll."""
    return [
        PayslipLine(
            key="basic_salary", label="Basic salary",
            amount=round(payroll.base_salary or 0.0, 2), taxable=True,
        ),
        PayslipLine(key="house_allowance", label="House allowance",
                    amount=round(payroll.house_allowance or 0.0, 2)),
        PayslipLine(key="transport_allowance", label="Transport allowance",
                    amount=round(payroll.transport_allowance or 0.0, 2)),
        PayslipLine(key="medical_allowance", label="Medical allowance",
                    amount=round(payroll.medical_allowance or 0.0, 2)),
        PayslipLine(key="other_allowance",
                    label=payroll.other_allowance_note or "Other allowance",
                    amount=round(payroll.other_allowance or 0.0, 2)),
        PayslipLine(key="bonus", label="Bonus",
                    amount=round(payroll.bonuses or 0.0, 2)),
        PayslipLine(key="arrears", label="Arrears",
                    amount=round(payroll.arrears or 0.0, 2)),
        PayslipLine(key="overtime", label="Overtime pay",
                    amount=round(payroll.overtime_pay or 0.0, 2),
                    # The formula only means something when hours were worked.
                    formula=_overtime_formula(db, payroll) if payroll.overtime_pay else None),
        PayslipLine(key="reimbursement", label="Reimbursements paid through payroll",
                    amount=round(payroll.reimbursement_total or 0.0, 2),
                    note="Approved expense claims for this period"),
    ]


def _build_deductions(payroll: Payroll) -> List[PayslipLine]:
    return [
        PayslipLine(key="income_tax", label="Income tax",
                    amount=round(payroll.tax or 0.0, 2),
                    note="Deducted on basic salary under the applicable slab"),
        PayslipLine(key="provident_fund", label="Provident fund",
                    amount=round(payroll.provident_fund or 0.0, 2)),
        PayslipLine(key="social_security", label="Social security",
                    amount=round(payroll.social_security or 0.0, 2)),
        PayslipLine(key="eobi", label="EOBI",
                    amount=round(payroll.eobi or 0.0, 2)),
        PayslipLine(key="loan_repayment", label="Loan repayment",
                    amount=round(payroll.loan_repayment or 0.0, 2),
                    note=payroll.loan_repayment_note),
        PayslipLine(key="advance_recovery", label="Advance salary recovery",
                    amount=round(payroll.advance_recovery or 0.0, 2),
                    note=payroll.advance_recovery_note),
        PayslipLine(key="unpaid_leave", label="Unpaid leave deduction",
                    amount=round(payroll.unpaid_leave_deduction or 0.0, 2),
                    note="Approved unpaid leave: basic salary / 160 hours x 8 hours per working day"),
        PayslipLine(key="unapproved_leave_fine", label="Unapproved leave fine",
                    amount=round(payroll.unapproved_leave_fine or 0.0, 2),
                    note="Manual Admin/HR penalty at Rs 5,000 per unapproved day"),
        PayslipLine(key="other_deduction",
                    # A named recovery reads far better than "other deduction".
                    label=payroll.other_deduction_note or "Other deduction",
                    amount=round(payroll.other_deduction or 0.0, 2)),
    ]


def _revision(payroll: Optional[Payroll]) -> Optional[PayslipRevision]:
    if not payroll:
        return None
    return PayslipRevision(
        id=payroll.id, payslip_no=payroll.payslip_no, version=payroll.version,
        status=payroll.status, generated_at=payroll.generated_at,
        correction_reason=payroll.correction_reason,
    )


def build_payslip_document(db: Session, payroll: Payroll) -> PayslipDetailResponse:
    employee = db.query(User).filter(User.id == payroll.employee_id).first()
    start, end = _period_bounds(payroll)
    figures = payslip_reconciliation(payroll)

    superseded_by = db.query(Payroll).filter(Payroll.supersedes_id == payroll.id).first()
    open_disputes = db.query(PayslipDispute).filter(
        PayslipDispute.payroll_id == payroll.id,
        PayslipDispute.status.in_([DisputeStatusEnum.OPEN, DisputeStatusEnum.IN_REVIEW]),
    ).count()

    return PayslipDetailResponse(
        id=payroll.id,
        employee_id=payroll.employee_id,
        payslip_no=payroll.payslip_no,
        verification_code=payroll.verification_code,
        version=payroll.version or 1,
        generated_at=payroll.generated_at,
        status=payroll.status,
        is_historical=bool(payroll.is_historical),
        month=payroll.month,
        year=payroll.year,
        period_label=_period_label(payroll),
        period_start=start,
        period_end=end,
        payment_date=payroll.payment_date,
        payment_method=payroll.payment_method,
        payment_reference=payroll.payment_reference,
        currency=settings.CURRENCY,
        company=PayslipCompany(
            name=settings.COMPANY_NAME,
            reg_no=settings.COMPANY_REG_NO,
            ntn=settings.COMPANY_REG_NO,
            address=settings.COMPANY_ADDRESS,
            email=settings.COMPANY_EMAIL,
        ),
        employee=PayslipEmployee(
            name=employee.full_name if employee else "Unknown",
            employee_id=employee.employee_id if employee else None,
            department=employee.department if employee else None,
            designation=employee.designation if employee else None,
            cnic=employee.cnic_number if employee else None,
            date_of_joining=employee.date_of_joining if employee else None,
            bank_account_masked=_mask_account(employee.account_number if employee else None),
            bank_name=employee.bank_number if employee else None,
        ),
        earnings=_build_earnings(db, payroll),
        deductions=_build_deductions(payroll),
        gross_earnings=figures["gross_earnings"],
        total_deductions=figures["total_deductions"],
        # Mirrors _recompute_payroll's `taxable_monthly` exactly: basic only,
        # after the approved unpaid-leave deduction, floored at zero.
        taxable_income=round(
            max(0.0, (payroll.base_salary or 0.0) - (payroll.unpaid_leave_deduction or 0.0)), 2
        ),
        net_salary=figures["net_salary"],
        reconciliation=PayslipReconciliation(**figures),
        supersedes=_revision(payroll.supersedes),
        superseded_by=_revision(superseded_by),
        correction_reason=payroll.correction_reason,
        cancellation_reason=payroll.cancellation_reason,
        open_dispute_count=open_disputes,
    )


# --------------------------------------------------------------------------
# read
# --------------------------------------------------------------------------

@router.get("/", response_model=List[PayslipSummary])
def list_payslips(
    year: Optional[int] = Query(None),
    employee_id: Optional[int] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Period list for the picker. Newest first."""
    target = _resolve_target_employee(db, current_user, employee_id)
    q = _visible_payslips(db, target, current_user)
    if year:
        q = q.filter(Payroll.year == year)
    records = sorted(q.all(), key=lambda p: (p.year, p.month), reverse=True)
    return [
        PayslipSummary(
            id=p.id, month=p.month, year=p.year,
            month_label=f"{MONTH_ABBR[p.month]} {p.year}",
            status=p.status, net_salary=round(p.net_salary or 0.0, 2),
            payslip_no=p.payslip_no, payment_date=p.payment_date,
        )
        for p in records
    ]


@router.get("/period", response_model=PayslipDetailResponse)
def get_payslip_for_period(
    year: int = Query(...),
    month: int = Query(..., ge=1, le=12),
    employee_id: Optional[int] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """The payslip for one month — how the page loads a period from the URL."""
    target = _resolve_target_employee(db, current_user, employee_id)
    records = _visible_payslips(db, target, current_user).filter(
        Payroll.year == year, Payroll.month == month,
    ).all()
    if not records:
        raise HTTPException(
            status_code=404,
            detail=f"No payslip has been generated for {MONTH_ABBR[month]} {year}.",
        )
    # A corrected period holds several versions; show the current one.
    current = max(records, key=lambda p: p.version or 1)
    return build_payslip_document(db, current)


@router.get("/ytd", response_model=PayslipYTD)
def get_year_to_date(
    year: int = Query(...),
    upto_month: int = Query(12, ge=1, le=12),
    employee_id: Optional[int] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Calendar-year totals up to and including a month.

    Superseded and cancelled versions are excluded so a corrected month is
    counted once, not twice.
    """
    target = _resolve_target_employee(db, current_user, employee_id)
    records = [
        p for p in _visible_payslips(db, target, current_user).filter(Payroll.year == year).all()
        if p.month <= upto_month
        and p.status not in (PayrollStatusEnum.CORRECTED, PayrollStatusEnum.CANCELLED)
    ]

    gross = sum(payslip_reconciliation(p)["gross_earnings"] for p in records)
    deductions = sum(payslip_reconciliation(p)["total_deductions"] for p in records)
    # Only basic pay is taxed; approved unpaid leave reduces that basic.
    taxable = sum(max(0.0, (p.base_salary or 0.0) - (p.unpaid_leave_deduction or 0.0)) for p in records)

    previous = [p for p in records if p.month == upto_month - 1]

    return PayslipYTD(
        year=year,
        currency=settings.CURRENCY,
        gross_earnings=round(gross, 2),
        total_deductions=round(deductions, 2),
        taxable_income=round(taxable, 2),
        tax_paid=round(sum(p.tax or 0.0 for p in records), 2),
        provident_fund=round(sum(p.provident_fund or 0.0 for p in records), 2),
        overtime_total=round(sum(p.overtime_pay or 0.0 for p in records), 2),
        bonus_total=round(sum(p.bonuses or 0.0 for p in records), 2),
        net_paid=round(sum(p.net_salary or 0.0 for p in records), 2),
        months_counted=len(records),
        previous_month_net=round(previous[0].net_salary or 0.0, 2) if previous else None,
    )


@router.get("/verify/{code}", response_model=PayslipVerificationResponse)
def verify_payslip(code: str, db: Session = Depends(get_db)):
    """Confirm a payslip is genuine from the code printed on it.

    Unauthenticated by design — the point is that a bank or landlord can check
    a document without an HRM account. It therefore returns only enough to
    prove authenticity, never the breakdown.
    """
    payroll = db.query(Payroll).filter(Payroll.verification_code == code).first()
    if not payroll or payroll.status not in FINAL_STATUSES + (PayrollStatusEnum.CORRECTED,):
        return PayslipVerificationResponse(valid=False)
    employee = db.query(User).filter(User.id == payroll.employee_id).first()
    return PayslipVerificationResponse(
        valid=True,
        payslip_no=payroll.payslip_no,
        employee_name=employee.full_name if employee else None,
        period_label=_period_label(payroll),
        status=payroll.status,
        version=payroll.version,
        generated_at=payroll.generated_at,
        net_salary=round(payroll.net_salary or 0.0, 2),
    )


@router.get("/{payroll_id:int}", response_model=PayslipDetailResponse)
def get_payslip(
    payroll_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return build_payslip_document(db, _load_payslip(db, payroll_id, current_user))


@router.get("/{payroll_id:int}/versions", response_model=List[PayslipRevision])
def get_payslip_versions(
    payroll_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Every version issued for this payslip's period, oldest first."""
    payroll = _load_payslip(db, payroll_id, current_user)
    records = _visible_payslips(db, payroll.employee_id, current_user).filter(
        Payroll.year == payroll.year, Payroll.month == payroll.month,
    ).all()
    return [_revision(p) for p in sorted(records, key=lambda p: p.version or 1)]


# --------------------------------------------------------------------------
# admin actions
# --------------------------------------------------------------------------

@router.post("/{payroll_id:int}/correct", response_model=PayslipDetailResponse)
def correct_payslip(
    payroll_id: int,
    body: PayslipCorrectionRequest,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin_or_hr),
):
    """Issue a corrected payslip as a new version.

    The original is kept and moved to CORRECTED rather than being edited in
    place — an employee may already have downloaded it, so the record of what
    they were given has to survive.
    """
    original = db.query(Payroll).filter(Payroll.id == payroll_id).first()
    if not original:
        raise HTTPException(status_code=404, detail="Payslip not found.")
    if original.status == PayrollStatusEnum.CANCELLED:
        raise HTTPException(status_code=400, detail="A cancelled payslip cannot be corrected.")
    if db.query(Payroll).filter(Payroll.supersedes_id == original.id).first():
        raise HTTPException(status_code=400, detail="This payslip has already been corrected.")
    if not body.reason.strip():
        raise HTTPException(status_code=400, detail="A reason is required to correct a payslip.")

    copied = {
        c.name: getattr(original, c.name)
        for c in Payroll.__table__.columns
        if c.name not in (
            "id", "payslip_no", "verification_code", "version", "generated_at",
            "supersedes_id", "correction_reason", "cancelled_at",
            "cancellation_reason", "created_at", "updated_at",
        )
    }
    revision = Payroll(**copied)
    revision.version = (original.version or 1) + 1
    revision.supersedes_id = original.id
    revision.correction_reason = body.reason.strip()
    revision.status = PayrollStatusEnum.DRAFT
    db.add(revision)

    original.status = PayrollStatusEnum.CORRECTED
    db.commit()
    db.refresh(revision)
    return build_payslip_document(db, revision)


@router.post("/{payroll_id:int}/cancel", response_model=PayslipDetailResponse)
def cancel_payslip(
    payroll_id: int,
    body: PayslipCancellationRequest,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin_or_hr),
):
    payroll = db.query(Payroll).filter(Payroll.id == payroll_id).first()
    if not payroll:
        raise HTTPException(status_code=404, detail="Payslip not found.")
    if payroll.status == PayrollStatusEnum.PAID:
        raise HTTPException(
            status_code=400,
            detail="A paid payslip cannot be cancelled — issue a correction instead.",
        )
    if not body.reason.strip():
        raise HTTPException(status_code=400, detail="A reason is required to cancel a payslip.")
    payroll.status = PayrollStatusEnum.CANCELLED
    payroll.cancelled_at = datetime.utcnow()
    payroll.cancellation_reason = body.reason.strip()
    db.commit()
    db.refresh(payroll)
    return build_payslip_document(db, payroll)


@router.post("/{payroll_id:int}/publish", response_model=PayslipDetailResponse)
def publish_payslip(
    payroll_id: int,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin_or_hr),
):
    """Finalise a draft and stamp its identity.

    Refuses to publish a payslip whose figures do not add up, which is the
    guarantee behind "earnings - deductions = net pay" on the document.
    """
    payroll = db.query(Payroll).filter(Payroll.id == payroll_id).first()
    if not payroll:
        raise HTTPException(status_code=404, detail="Payslip not found.")

    figures = payslip_reconciliation(payroll)
    if not figures["balanced"]:
        raise HTTPException(
            status_code=400,
            detail=(
                "Payslip does not reconcile: "
                f"{figures['gross_earnings']} - {figures['total_deductions']} = "
                f"{figures['expected_net']}, but net pay is {figures['net_salary']}."
            ),
        )

    was_final = payroll.status in FINAL_STATUSES
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
    return build_payslip_document(db, payroll)


@router.post("/{payroll_id:int}/email", status_code=status.HTTP_202_ACCEPTED)
def email_payslip(
    payroll_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Email the payslip summary to the employee's registered address.

    Sent only to the address on file, never to one supplied in the request, so
    this cannot be used to forward someone else's salary details.
    """
    payroll = _load_payslip(db, payroll_id, current_user)
    if payroll.status not in FINAL_STATUSES:
        raise HTTPException(status_code=400, detail="Only a finalised payslip can be emailed.")
    employee = db.query(User).filter(User.id == payroll.employee_id).first()
    if not employee or not employee.email:
        raise HTTPException(status_code=400, detail="This employee has no email address on file.")

    doc = build_payslip_document(db, payroll)
    try:
        send_payslip_email(employee.email, doc, f"{MONTH_ABBR[doc.month]} {doc.year}")
    except Exception as exc:  # noqa: BLE001 - surfaced to the caller as a 502
        logging.getLogger(__name__).error("Failed to send payslip email to %s: %s", employee.email, exc)
        raise HTTPException(status_code=502, detail="Could not send the payslip email. Please try again later.")
    return {"sent_to": employee.email}


# --------------------------------------------------------------------------
# disputes
# --------------------------------------------------------------------------

def _dispute_response(db: Session, dispute: PayslipDispute) -> DisputeResponse:
    employee = db.query(User).filter(User.id == dispute.employee_id).first()
    responder = (
        db.query(User).filter(User.id == dispute.responded_by).first()
        if dispute.responded_by else None
    )
    payroll = db.query(Payroll).filter(Payroll.id == dispute.payroll_id).first()
    return DisputeResponse(
        id=dispute.id,
        payroll_id=dispute.payroll_id,
        employee_id=dispute.employee_id,
        employee_name=employee.full_name if employee else None,
        payslip_no=payroll.payslip_no if payroll else None,
        period_label=_period_label(payroll) if payroll else None,
        category=dispute.category,
        comment=dispute.comment,
        attachment_url=dispute.attachment_url,
        status=dispute.status,
        response=dispute.response,
        responder_name=responder.full_name if responder else None,
        responded_at=dispute.responded_at,
        created_at=dispute.created_at,
    )


@router.get("/disputes/list", response_model=List[DisputeResponse])
def list_disputes(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """An employee sees their own queries; an admin sees the whole queue."""
    q = db.query(PayslipDispute)
    if not _is_privileged(current_user):
        q = q.filter(PayslipDispute.employee_id == current_user.id)
    disputes = q.order_by(PayslipDispute.created_at.desc()).all()
    return [_dispute_response(db, d) for d in disputes]


@router.post("/{payroll_id:int}/disputes", response_model=DisputeResponse,
             status_code=status.HTTP_201_CREATED)
def raise_dispute(
    payroll_id: int,
    body: DisputeCreate,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    payroll = _load_payslip(db, payroll_id, current_user)
    if not body.comment.strip():
        raise HTTPException(status_code=400, detail="Describe the issue before submitting.")

    dispute = PayslipDispute(
        payroll_id=payroll.id,
        employee_id=payroll.employee_id,
        category=body.category,
        comment=body.comment.strip(),
        attachment_url=body.attachment_url,
    )
    db.add(dispute)
    db.commit()
    db.refresh(dispute)

    # Admin/HR need to actually see this: an in-app notification for whoever
    # has the dashboard open right now, and an email for whenever they don't
    # — this used to go nowhere but the disputes table, so a query could sit
    # unseen indefinitely.
    category_value = body.category.value if hasattr(body.category, "value") else str(body.category)
    category_label = DISPUTE_CATEGORY_LABELS.get(category_value, category_value.replace("_", " ").title())
    period_label = f"{MONTH_ABBR[payroll.month]} {payroll.year}"
    employee_name = current_user.full_name

    notify_admins(
        db,
        title="Payslip query raised",
        message=f"{employee_name} raised a query on their {period_label} payslip: {category_label}.",
        notification_type=NotificationTypeEnum.WARNING,
        related_id=payroll.id,
        related_type=RelatedTypeEnum.PAYSLIP_DISPUTE,
        exclude_user_id=current_user.id,
    )

    admin_emails = [
        row[0] for row in db.query(User.email).filter(
            or_(User.role == RoleEnum.ADMIN, User.is_hr == True),  # noqa: E712
            User.is_active == True,  # noqa: E712
        ).all()
        if row[0]
    ]
    background_tasks.add_task(
        send_payslip_dispute_email,
        admin_emails, employee_name, category_label, dispute.comment, period_label, dispute.attachment_url,
    )

    return _dispute_response(db, dispute)


@router.put("/disputes/{dispute_id:int}/respond", response_model=DisputeResponse)
def respond_to_dispute(
    dispute_id: int,
    body: DisputeRespond,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin_or_hr),
):
    dispute = db.query(PayslipDispute).filter(PayslipDispute.id == dispute_id).first()
    if not dispute:
        raise HTTPException(status_code=404, detail="Query not found.")
    dispute.status = body.status
    if body.response is not None:
        dispute.response = body.response.strip()
    dispute.responded_by = admin.id
    dispute.responded_at = datetime.utcnow()
    db.commit()
    db.refresh(dispute)

    # The employee raised this and has no other way to know it was looked
    # at — without this they'd have to keep re-opening the payslip to check.
    payroll = db.query(Payroll).filter(Payroll.id == dispute.payroll_id).first()
    period_label = f"{MONTH_ABBR[payroll.month]} {payroll.year}" if payroll else "your payslip"
    status_value = body.status.value if hasattr(body.status, "value") else str(body.status)
    status_label = status_value.replace("_", " ").capitalize()
    message = f"Your query on the {period_label} payslip is now {status_label.lower()}."
    if dispute.response:
        message += f" Response: {dispute.response}"
    create_notification(
        db=db,
        user_id=dispute.employee_id,
        title=f"Payslip query {status_label.lower()}",
        message=message,
        notification_type=NotificationTypeEnum.SUCCESS if status_value == "resolved" else NotificationTypeEnum.INFO,
        related_id=dispute.payroll_id,
        related_type=RelatedTypeEnum.PAYSLIP_DISPUTE,
    )

    return _dispute_response(db, dispute)
