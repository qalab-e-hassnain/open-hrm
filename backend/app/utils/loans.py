"""
Schedule arithmetic for advances and loans, and the payroll sweep that recovers
them.

Kept out of the router so the payroll recompute can call it without importing
an API module.
"""
from datetime import datetime
from typing import List, Optional

from sqlalchemy.orm import Session, joinedload

from app.models.loan import (EmployeeLoan, InstalmentStatusEnum, LoanInstalment,
                             LoanKindEnum, LoanStatusEnum)


def month_key(year: int, month: int) -> str:
    return f"{year:04d}-{month:02d}"


def add_months(key: str, n: int) -> str:
    year, month = (int(p) for p in key.split("-"))
    total = (year * 12 + (month - 1)) + n
    return month_key(total // 12, total % 12 + 1)


def build_schedule(loan: EmployeeLoan) -> List[LoanInstalment]:
    """
    Split the principal into instalments.

    Any rounding remainder lands on the FIRST instalment rather than the last.
    Recovering a stray rupee at the end would leave a loan looking unsettled
    for a whole extra month, and the earlier deduction is the one the employee
    has already budgeted for.
    """
    count = max(1, int(loan.instalment_count or 1))
    principal = round(float(loan.principal or 0.0), 2)
    base = round(principal / count, 2)
    remainder = round(principal - base * count, 2)

    rows = []
    for i in range(count):
        amount = base + (remainder if i == 0 else 0.0)
        rows.append(LoanInstalment(
            loan_id=loan.id,
            sequence=i + 1,
            due_month=add_months(loan.first_recovery_month, i),
            amount=round(amount, 2),
            status=InstalmentStatusEnum.PENDING,
        ))
    return rows


def outstanding_for(db: Session, employee_id: int) -> float:
    """Everything this employee still owes across all their loans."""
    return outstanding_for_many(db, [employee_id]).get(employee_id, 0.0)


def outstanding_for_many(db: Session, employee_ids) -> dict:
    """
    Outstanding balances for several people at once, as {employee_id: amount}.

    A company-wide total asked this one employee at a time, which on the
    analytics page was twenty round trips to add up twenty numbers. Everyone
    who appears in `employee_ids` gets an entry, including the many who owe
    nothing.
    """
    ids = list({int(i) for i in employee_ids})
    totals = {i: 0.0 for i in ids}
    if not ids:
        return totals
    loans = (db.query(EmployeeLoan)
             .options(joinedload(EmployeeLoan.instalments))
             .filter(EmployeeLoan.employee_id.in_(ids),
                     EmployeeLoan.status == LoanStatusEnum.DISBURSED)
             .all())
    for loan in loans:
        totals[loan.employee_id] = totals.get(loan.employee_id, 0.0) + loan.outstanding
    return {i: round(v, 2) for i, v in totals.items()}


def refresh_settlement(db: Session, loan: EmployeeLoan) -> None:
    """Mark a loan settled once nothing is left pending."""
    if loan.status != LoanStatusEnum.DISBURSED:
        return
    if any(i.status == InstalmentStatusEnum.PENDING for i in loan.instalments):
        return
    loan.status = LoanStatusEnum.SETTLED
    loan.settled_at = datetime.utcnow()


def sweep_for_payroll(db: Session, payroll) -> dict:
    """
    Attach this month's due instalments to a payslip.

    Absolute rather than additive: the deduction lines are set to what is due
    this month, so calling it again on the same payslip changes nothing. That
    matters because the payroll recompute runs on every edit.

    Returns the two figures for the caller to place on the payslip; it does not
    touch the payroll row itself, which keeps the deduction reconciliation in
    one place.
    """
    key = month_key(payroll.year, payroll.month)
    due = (db.query(LoanInstalment)
           .join(EmployeeLoan, LoanInstalment.loan_id == EmployeeLoan.id)
           .options(joinedload(LoanInstalment.loan))
           .filter(EmployeeLoan.employee_id == payroll.employee_id,
                   EmployeeLoan.status == LoanStatusEnum.DISBURSED,
                   LoanInstalment.due_month == key,
                   LoanInstalment.status != InstalmentStatusEnum.WAIVED)
           .all())

    advance, loan_amount = 0.0, 0.0
    advance_notes, loan_notes = [], []
    for inst in due:
        # Already recovered on a *different* payslip — leave it alone.
        if (inst.status == InstalmentStatusEnum.RECOVERED
                and inst.payroll_id not in (None, payroll.id)):
            continue
        inst.status = InstalmentStatusEnum.RECOVERED
        inst.payroll_id = payroll.id
        inst.recovered_at = inst.recovered_at or datetime.utcnow()
        note = f"Instalment {inst.sequence} of {inst.loan.instalment_count}"
        if inst.loan.kind == LoanKindEnum.ADVANCE:
            advance += inst.amount
            advance_notes.append(note)
        else:
            loan_amount += inst.amount
            loan_notes.append(note)
        refresh_settlement(db, inst.loan)

    return {
        "advance_recovery": round(advance, 2),
        "advance_recovery_note": "; ".join(advance_notes) or None,
        "loan_repayment": round(loan_amount, 2),
        "loan_repayment_note": "; ".join(loan_notes) or None,
    }


def release_payroll(db: Session, payroll_id: int) -> int:
    """
    Put instalments back to pending when the payslip that carried them goes
    away. Without this, deleting a draft payslip would quietly forgive the
    deduction it was holding.
    """
    rows = (db.query(LoanInstalment)
            .filter(LoanInstalment.payroll_id == payroll_id).all())
    for inst in rows:
        inst.payroll_id = None
        inst.recovered_at = None
        if inst.status == InstalmentStatusEnum.RECOVERED:
            inst.status = InstalmentStatusEnum.PENDING
            if inst.loan and inst.loan.status == LoanStatusEnum.SETTLED:
                inst.loan.status = LoanStatusEnum.DISBURSED
                inst.loan.settled_at = None
    return len(rows)
