"""Monthly payroll sheet: a run per month, one entry per employee.

Two invariants this module exists to protect:
  1. A finalized run is immutable — every mutating route rejects with 409
     unless run.status == draft. That's the audit boundary.
  2. PayrollEntry.monthly_salary is a SNAPSHOT taken at entry creation. It is
     never re-read from User.base_salary afterwards, so a later raise cannot
     rewrite a historical sheet.
"""
import calendar
from io import BytesIO
from datetime import date, datetime
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import or_
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.models.payroll_run import (
    PayrollRun,
    PayrollEntry,
    PayrollAuditLog,
    PayrollRunStatusEnum,
)
from app.models.payroll import Payroll, PayrollStatusEnum
from app.models.user import User
from app.api.deps import require_admin_or_hr
from app.schemas.payroll_run import (
    PayrollRunCreate,
    PayrollRunSummary,
    PayrollRunDetail,
    PayrollRunReopen,
    PayrollEntryAdd,
    PayrollEntryUpdate,
    PayrollEntryResponse,
    PayrollAuditLogResponse,
)

router = APIRouter()

# The adjustable numeric columns — the only fields PATCH may write (plus notes).
# monthly_salary is deliberately absent: it's a snapshot.
ADJUSTABLE_NUMERIC_FIELDS = (
    "increment",
    "allowance_overtime",
    "arrears",
    "bonus",
    "tax_deduction",
    "other_deduction",
)


def _get_run(db: Session, run_id: int, *, lock: bool = False) -> PayrollRun:
    """
    One payroll run, optionally locked for the rest of this transaction.

    `lock=True` takes a row lock, and every path that changes a run uses it.
    Without one, two people finalizing the same month both read it as a draft,
    both pass the draft check, and both generate a payslip per employee: one
    transaction wins, the other dies on the unique index with a 500, and
    whoever pressed the button is left not knowing whether anybody got paid.

    With the lock the second request waits for the first to commit, then reads
    the run as finalized and is told so plainly.

    SQLite ignores FOR UPDATE, which is why this is also covered by a test
    that runs against PostgreSQL.
    """
    query = db.query(PayrollRun).filter(PayrollRun.id == run_id)
    if lock:
        query = query.with_for_update()
    run = query.first()
    if not run:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Payroll run not found",
        )
    return run


def _require_draft(run: PayrollRun) -> None:
    """The audit boundary: nothing about a finalized/paid run may change."""
    if run.status != PayrollRunStatusEnum.DRAFT:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Payroll run for {run.month} is {run.status.value} and can no longer be edited. Reopen it first.",
        )


def _entry_to_response(entry: PayrollEntry) -> PayrollEntryResponse:
    employee = entry.employee
    return PayrollEntryResponse(
        id=entry.id,
        payroll_run_id=entry.payroll_run_id,
        employee_id=entry.employee_id,
        employee_name=employee.full_name if employee else f"Employee #{entry.employee_id}",
        designation=employee.designation if employee else None,
        monthly_salary=entry.monthly_salary or 0.0,
        increment=entry.increment or 0.0,
        allowance_overtime=entry.allowance_overtime or 0.0,
        arrears=entry.arrears or 0.0,
        bonus=entry.bonus or 0.0,
        tax_deduction=entry.tax_deduction or 0.0,
        other_deduction=entry.other_deduction or 0.0,
        notes=entry.notes,
        gross_salary=entry.gross_salary,
        gross_total=entry.gross_total,
        net_payable=entry.net_payable,
        updated_at=entry.updated_at,
    )


@router.post("/runs", response_model=PayrollRunDetail, status_code=status.HTTP_201_CREATED)
def create_run(
    payload: PayrollRunCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin_or_hr),
):
    """Create a draft run for a month with one entry per active employee."""
    existing = db.query(PayrollRun).filter(PayrollRun.month == payload.month).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"A payroll run for {payload.month} already exists.",
        )

    run = PayrollRun(
        month=payload.month,
        status=PayrollRunStatusEnum.DRAFT,
        created_by=current_user.id,
    )
    db.add(run)
    db.flush()  # assign run.id without ending the transaction

    # Anyone active, plus anyone whose last working day falls inside this
    # month. Filtering on "active today" alone means someone who left on the
    # 15th is deactivated before the month's payroll is run in the following
    # month, and their final salary is silently skipped — the leaver is exactly
    # the person who notices.
    from app.models.lifecycle import Resignation, ResignationStatusEnum

    year, month = _run_period(payload.month)
    month_start = date(year, month, 1)
    month_end = date(year, month, calendar.monthrange(year, month)[1])

    leavers_this_month = {
        r.employee_id
        for r in db.query(Resignation).filter(
            Resignation.status == ResignationStatusEnum.COMPLETED
        ).all()
        if (r.agreed_last_working_day or r.proposed_last_working_day)
        and month_start <= (r.agreed_last_working_day or r.proposed_last_working_day) <= month_end
    }

    employees = (
        db.query(User)
        .filter(or_(User.is_active == True,  # noqa: E712 — SQLAlchemy needs ==, not `is`
                    User.id.in_(leavers_this_month) if leavers_this_month else False))
        .order_by(User.full_name)
        .all()
    )
    # Carry the previous month's tax across. It is a standing figure per
    # employee — pension-scheme exemptions and the like don't change month to
    # month — so retyping it for everyone each month invites errors. It stays
    # editable; this only decides what the sheet opens with. Increments are NOT
    # carried: they are already folded into base_salary on finalize, so
    # copying them too would apply the same raise a second time.
    previous = (
        db.query(PayrollRun)
        .filter(PayrollRun.month < payload.month)
        .order_by(PayrollRun.month.desc())
        .first()
    )
    prior_tax = {}
    if previous:
        prior_tax = {
            e.employee_id: e.tax_deduction or 0.0
            for e in db.query(PayrollEntry).filter(
                PayrollEntry.payroll_run_id == previous.id
            ).all()
        }

    for employee in employees:
        db.add(
            PayrollEntry(
                payroll_run_id=run.id,
                employee_id=employee.id,
                # Snapshot — see module docstring.
                monthly_salary=employee.base_salary or 0.0,
                tax_deduction=prior_tax.get(employee.id, 0.0),
            )
        )

    db.commit()
    db.refresh(run)
    return _run_detail(db, run)


def _run_detail(db: Session, run: PayrollRun) -> PayrollRunDetail:
    entries = (
        db.query(PayrollEntry)
        .options(joinedload(PayrollEntry.employee))
        .filter(PayrollEntry.payroll_run_id == run.id)
        .all()
    )
    entries.sort(key=lambda e: (e.employee.full_name if e.employee else ""))
    responses = [_entry_to_response(e) for e in entries]
    return PayrollRunDetail(
        id=run.id,
        month=run.month,
        status=run.status,
        created_by=run.created_by,
        created_at=run.created_at,
        finalized_at=run.finalized_at,
        entries=responses,
        total_gross=round(sum(r.gross_total for r in responses), 2),
        total_net=round(sum(r.net_payable for r in responses), 2),
    )


@router.get("/runs", response_model=List[PayrollRunSummary])
def list_runs(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin_or_hr),
):
    """List runs, newest month first. Totals computed on the fly, never stored."""
    runs = db.query(PayrollRun).order_by(PayrollRun.month.desc()).all()
    summaries: List[PayrollRunSummary] = []
    for run in runs:
        entries = db.query(PayrollEntry).filter(PayrollEntry.payroll_run_id == run.id).all()
        summaries.append(
            PayrollRunSummary(
                id=run.id,
                month=run.month,
                status=run.status,
                created_at=run.created_at,
                finalized_at=run.finalized_at,
                employee_count=len(entries),
                total_gross=round(sum(e.gross_total for e in entries), 2),
                total_net=round(sum(e.net_payable for e in entries), 2),
            )
        )
    return summaries


@router.get("/runs/{run_id}", response_model=PayrollRunDetail)
def get_run(
    run_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin_or_hr),
):
    """Full run with every entry, computed fields and joined employee details."""
    return _run_detail(db, _get_run(db, run_id))


@router.patch("/runs/{run_id}/entries/{entry_id}", response_model=PayrollEntryResponse)
def update_entry(
    run_id: int,
    entry_id: int,
    payload: PayrollEntryUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin_or_hr),
):
    """Partial update of adjustable fields. 409 on a non-draft run.
    Writes one audit_log row per field actually changed."""
    run = _get_run(db, run_id, lock=True)
    _require_draft(run)

    entry = (
        db.query(PayrollEntry)
        .filter(PayrollEntry.id == entry_id, PayrollEntry.payroll_run_id == run_id)
        .first()
    )
    if not entry:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Payroll entry not found on this run",
        )

    # exclude_unset so an omitted field is left alone rather than nulled.
    changes = payload.model_dump(exclude_unset=True)

    for field, new_value in changes.items():
        old_value = getattr(entry, field)

        if field == "notes":
            if (old_value or "") == (new_value or ""):
                continue
            setattr(entry, field, new_value)
            db.add(
                PayrollAuditLog(
                    payroll_entry_id=entry.id,
                    payroll_run_id=run.id,
                    field_changed="notes",
                    note=f"{old_value or '(empty)'} -> {new_value or '(empty)'}",
                    changed_by=current_user.id,
                )
            )
            continue

        if field not in ADJUSTABLE_NUMERIC_FIELDS:
            continue  # never trust the client to name a non-adjustable column

        if new_value is None:
            continue
        new_value = float(new_value)
        if float(old_value or 0.0) == new_value:
            continue  # no-op: don't pollute the audit trail

        setattr(entry, field, new_value)
        db.add(
            PayrollAuditLog(
                payroll_entry_id=entry.id,
                payroll_run_id=run.id,
                field_changed=field,
                old_value=float(old_value or 0.0),
                new_value=new_value,
                changed_by=current_user.id,
            )
        )

    db.commit()
    db.refresh(entry)
    return _entry_to_response(entry)


@router.post("/runs/{run_id}/entries", response_model=PayrollEntryResponse, status_code=status.HTTP_201_CREATED)
def add_entry(
    run_id: int,
    payload: PayrollEntryAdd,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin_or_hr),
):
    """Add an employee to an existing draft run."""
    run = _get_run(db, run_id, lock=True)
    _require_draft(run)

    employee = db.query(User).filter(User.id == payload.employee_id).first()
    if not employee:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Employee not found",
        )

    duplicate = (
        db.query(PayrollEntry)
        .filter(
            PayrollEntry.payroll_run_id == run_id,
            PayrollEntry.employee_id == payload.employee_id,
        )
        .first()
    )
    if duplicate:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"{employee.full_name} is already on this payroll run.",
        )

    entry = PayrollEntry(
        payroll_run_id=run.id,
        employee_id=employee.id,
        monthly_salary=employee.base_salary or 0.0,  # snapshot
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return _entry_to_response(entry)


@router.delete("/runs/{run_id}/entries/{entry_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_entry(
    run_id: int,
    entry_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin_or_hr),
):
    """Remove a row from a draft run."""
    run = _get_run(db, run_id, lock=True)
    _require_draft(run)

    entry = (
        db.query(PayrollEntry)
        .filter(PayrollEntry.id == entry_id, PayrollEntry.payroll_run_id == run_id)
        .first()
    )
    if not entry:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Payroll entry not found on this run",
        )

    db.delete(entry)
    db.commit()
    return None


# ---------------------------------------------------------------------------
# Payslip generation from a finalized run
# ---------------------------------------------------------------------------

def _run_period(month: str) -> tuple:
    """"2026-08" -> (2026, 8)."""
    year_str, month_str = month.split("-")
    return int(year_str), int(month_str)


def _generate_payslips(db: Session, run: PayrollRun, actor: User) -> tuple:
    """
    Turn every entry on a finalized run into that employee's payslip.

    The run is the source of truth, so each figure is copied across rather than
    re-derived, and the tax is pinned (tax_is_manual). Pension-scheme
    contributions are exempt from salary tax and the slab engine has no way to
    know about them, so recomputing here would replace a correct figure with a
    higher wrong one.

    Re-finalizing after a reopen updates the payslips in place instead of
    creating duplicates, and bumps the version so the employee's revision
    history records that the figures moved.

    Returns (created, updated).
    """
    from app.api.v1.payroll import (
        _assign_payslip_identity, _recompute_payroll, queue_payslip_notification,
    )

    year, month = _run_period(run.month)
    entries = db.query(PayrollEntry).filter(PayrollEntry.payroll_run_id == run.id).all()
    created = updated = 0

    for entry in entries:
        employee = db.query(User).filter(User.id == entry.employee_id).first()
        if not employee:
            continue

        # An increment is paid on top of the snapshot salary this month, and
        # becomes part of the salary from here on (see _carry_forward).
        pay_for_period = (entry.monthly_salary or 0.0) + (entry.increment or 0.0)
        if pay_for_period <= 0:
            continue

        payslip = db.query(Payroll).filter(
            Payroll.employee_id == entry.employee_id,
            Payroll.month == month,
            Payroll.year == year,
        ).first()

        is_new = payslip is None
        before = None if is_new else (
            payslip.base_salary, payslip.allowances, payslip.medical_allowance,
            payslip.other_allowance, payslip.bonuses, payslip.arrears,
            payslip.deductions, payslip.tax,
        )
        if is_new:
            payslip = Payroll(
                employee_id=entry.employee_id, month=month, year=year,
                net_salary=0.0, status=PayrollStatusEnum.DRAFT,
            )
            db.add(payslip)

        # Split pay the way the company's slip structure defines it: Basic Pay
        # is Gross/1.1, leaving a Medical Allowance of exactly 10% of basic,
        # which is exempt from salary tax under Clause 139(1)(b) of the Second
        # Schedule. The tax entered on the run already assumes this, so a
        # payslip that showed the whole amount as taxable basic would state a
        # taxable figure its own tax line contradicts.
        basic = round(pay_for_period / 1.1, 2)
        medical = round(pay_for_period - basic, 2)
        other_allow = entry.allowance_overtime or 0.0

        payslip.base_salary = basic
        payslip.medical_allowance = medical
        payslip.other_allowance = other_allow
        if other_allow:
            payslip.other_allowance_note = "Allowance / overtime (payroll sheet)"
        payslip.allowances = round(medical + other_allow, 2)
        payslip.bonuses = entry.bonus or 0.0
        payslip.arrears = entry.arrears or 0.0
        payslip.deductions = entry.other_deduction or 0.0
        payslip.tax = entry.tax_deduction or 0.0
        payslip.tax_is_manual = True
        payslip.notes = entry.notes or payslip.notes

        db.flush()  # need payslip.id before approved items can be stamped to it
        _recompute_payroll(db, payslip)

        if is_new:
            _assign_payslip_identity(db, payslip)
            payslip.status = PayrollStatusEnum.PROCESSED
            queue_payslip_notification(
                db, user_id=payslip.employee_id, payroll_id=payslip.id,
                month=month, year=year,
            )
            created += 1
        else:
            after = (
                payslip.base_salary, payslip.allowances, payslip.medical_allowance,
                payslip.other_allowance, payslip.bonuses, payslip.arrears,
                payslip.deductions, payslip.tax,
            )
            # Only a real change is a revision. Re-finalizing a run to add one
            # employee used to advance the version on everybody else's payslip
            # too, implying each had been revised when not a figure had moved.
            if after != before:
                payslip.version = (payslip.version or 1) + 1
                updated += 1

    return created, updated


def _carry_forward(db: Session, run: PayrollRun) -> int:
    """
    Make the sheet's pay the employee's standing salary.

    Next month's run seeds monthly_salary from User.base_salary, so anything
    settled on this sheet has to land back on the employee or it is paid once
    and quietly disappears — an increment, but equally a corrected salary an
    admin typed straight into monthly_salary.

    Only the latest run does this. Re-finalizing an old month must not push a
    superseded figure over a raise that has happened since.
    """
    latest = (db.query(PayrollRun).order_by(PayrollRun.month.desc()).first())
    if latest and latest.id != run.id:
        return 0

    moved = 0
    for entry in db.query(PayrollEntry).filter(PayrollEntry.payroll_run_id == run.id).all():
        employee = db.query(User).filter(User.id == entry.employee_id).first()
        if not employee:
            continue
        new_salary = (entry.monthly_salary or 0.0) + (entry.increment or 0.0)
        if new_salary > 0 and employee.base_salary != new_salary:
            employee.base_salary = new_salary
            moved += 1
    return moved


@router.post("/runs/{run_id}/finalize", response_model=PayrollRunDetail)
def finalize_run(
    run_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin_or_hr),
):
    """Close the run for editing."""
    run = _get_run(db, run_id, lock=True)
    _require_draft(run)

    run.status = PayrollRunStatusEnum.FINALIZED
    run.finalized_at = datetime.utcnow()

    # Finalizing is what issues pay: generate a payslip per entry and fold any
    # increment into the standing salary, so next month's run starts from the
    # new figure rather than silently reverting.
    created, updated = _generate_payslips(db, run, current_user)
    raised = _carry_forward(db, run)

    db.add(
        PayrollAuditLog(
            payroll_run_id=run.id,
            field_changed="run_finalized",
            note=(
                f"Run {run.month} finalized — {created} payslip(s) issued, "
                f"{updated} regenerated, {raised} salary increment(s) carried forward"
            ),
            changed_by=current_user.id,
        )
    )
    db.commit()
    db.refresh(run)
    return _run_detail(db, run)


@router.post("/runs/{run_id}/reopen", response_model=PayrollRunDetail)
def reopen_run(
    run_id: int,
    payload: PayrollRunReopen,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin_or_hr),
):
    """Reopen a finalized run for editing. The reason is audited."""
    run = _get_run(db, run_id, lock=True)
    if run.status == PayrollRunStatusEnum.DRAFT:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Payroll run for {run.month} is already a draft.",
        )

    previous_status = run.status.value
    run.status = PayrollRunStatusEnum.DRAFT
    run.finalized_at = None
    db.add(
        PayrollAuditLog(
            payroll_run_id=run.id,
            field_changed="run_reopened",
            note=f"Reopened from {previous_status}. Reason: {payload.reason}",
            changed_by=current_user.id,
        )
    )
    db.commit()
    db.refresh(run)
    return _run_detail(db, run)


@router.delete("/runs/{run_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_run(
    run_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin_or_hr),
):
    """Delete a run — drafts only."""
    run = _get_run(db, run_id, lock=True)
    _require_draft(run)

    # Audit rows point at the run/entries being removed; clear them first so the
    # delete doesn't trip the FK.
    db.query(PayrollAuditLog).filter(PayrollAuditLog.payroll_run_id == run_id).delete(
        synchronize_session=False
    )
    db.delete(run)  # cascades to entries
    db.commit()
    return None


@router.get("/runs/{run_id}/audit", response_model=List[PayrollAuditLogResponse])
def get_run_audit(
    run_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin_or_hr),
):
    """Full audit trail for a run, newest first."""
    _get_run(db, run_id)
    return (
        db.query(PayrollAuditLog)
        .filter(PayrollAuditLog.payroll_run_id == run_id)
        .order_by(PayrollAuditLog.changed_at.desc())
        .all()
    )


# Column order is contractual — it mirrors the manual payroll sheet.
EXPORT_COLUMNS = [
    "Sr#",
    "Name",
    "Designation",
    "Monthly Salary",
    "Increment",
    "Gross Salary",
    "Allowance/Overtime",
    "Arrears",
    "Bonus",
    "Gross Total",
    "Tax Deduction",
    "Advance/Other Deduction",
    "Net Payable",
]


@router.get("/runs/{run_id}/export")
def export_run(
    run_id: int,
    format: str = Query("xlsx", description="Only xlsx is supported"),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin_or_hr),
):
    """Stream the run as an .xlsx matching the manual payroll sheet layout."""
    if format != "xlsx":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only format=xlsx is supported",
        )

    try:
        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    except ImportError:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="Excel export needs the openpyxl package installed on the server.",
        )

    run = _get_run(db, run_id)
    detail = _run_detail(db, run)

    wb = Workbook()
    ws = wb.active
    ws.title = "Payroll"

    thin = Side(style="thin", color="999999")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    money_fmt = "#,##0.00"

    # Title rows, matching the manual sheet's header block.
    last_col = len(EXPORT_COLUMNS)
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=last_col)
    # The company running this deployment, not whoever wrote the code. This
    # is the header of a payroll sheet that goes to a bank and an auditor.
    ws.cell(row=1, column=1,
            value=settings.COMPANY_NAME.upper()).font = Font(bold=True, size=12)
    ws.cell(row=1, column=1).alignment = Alignment(horizontal="center")

    period = datetime.strptime(run.month, "%Y-%m").strftime("%b %y").upper()
    ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=last_col)
    ws.cell(row=2, column=1, value=f"PAYROLL FOR THE MONTH OF {period}").font = Font(bold=True, size=11)
    ws.cell(row=2, column=1).alignment = Alignment(horizontal="center")

    header_row = 4
    header_fill = PatternFill("solid", fgColor="F2F2F2")
    for col, name in enumerate(EXPORT_COLUMNS, start=1):
        cell = ws.cell(row=header_row, column=col, value=name)
        cell.font = Font(bold=True, size=9)
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.fill = header_fill
        cell.border = border

    row_num = header_row + 1
    for index, e in enumerate(detail.entries, start=1):
        values = [
            index,
            e.employee_name,
            e.designation or "",
            e.monthly_salary,
            e.increment,
            e.gross_salary,
            e.allowance_overtime,
            e.arrears,
            e.bonus,
            e.gross_total,
            e.tax_deduction,
            e.other_deduction,
            e.net_payable,
        ]
        for col, value in enumerate(values, start=1):
            cell = ws.cell(row=row_num, column=col, value=value)
            cell.border = border
            cell.font = Font(size=9)
            if col >= 4:
                cell.number_format = money_fmt
            elif col == 1:
                cell.alignment = Alignment(horizontal="center")
        row_num += 1

    # Totals row.
    total_cell = ws.cell(row=row_num, column=3, value="Total:")
    total_cell.font = Font(bold=True, size=9)
    total_cell.alignment = Alignment(horizontal="right")
    totals = [
        sum(e.monthly_salary for e in detail.entries),
        sum(e.increment for e in detail.entries),
        sum(e.gross_salary for e in detail.entries),
        sum(e.allowance_overtime for e in detail.entries),
        sum(e.arrears for e in detail.entries),
        sum(e.bonus for e in detail.entries),
        sum(e.gross_total for e in detail.entries),
        sum(e.tax_deduction for e in detail.entries),
        sum(e.other_deduction for e in detail.entries),
        sum(e.net_payable for e in detail.entries),
    ]
    for offset, total in enumerate(totals):
        cell = ws.cell(row=row_num, column=4 + offset, value=total)
        cell.font = Font(bold=True, size=9)
        cell.number_format = money_fmt
        cell.border = Border(top=Side(style="thin"), bottom=Side(style="double"))

    widths = [6, 26, 18, 15, 12, 15, 16, 12, 12, 15, 14, 16, 17]
    for col, width in enumerate(widths, start=1):
        ws.column_dimensions[ws.cell(row=header_row, column=col).column_letter].width = width

    buffer = BytesIO()
    wb.save(buffer)
    buffer.seek(0)

    filename = f"payroll-{run.month}.xlsx"
    return StreamingResponse(
        buffer,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
