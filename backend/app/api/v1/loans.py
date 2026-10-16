"""
Salary advances and staff loans: request, approve, disburse, recover.

An employee may ask for one; only admin or HR decides. Disbursement is what
starts the schedule, so an approved-but-unpaid loan deducts nothing.
"""
from datetime import date, datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session, joinedload

from app.api.deps import get_current_user, is_privileged, require_admin_or_hr
from app.database import get_db
from app.models.loan import (EmployeeLoan, InstalmentStatusEnum, LoanInstalment,
                             LoanKindEnum, LoanStatusEnum)
from app.models.notification import NotificationTypeEnum
from app.models.user import User
from app.utils import audit
from app.utils.loans import build_schedule, month_key, outstanding_for, refresh_settlement
from app.utils.notifications import create_notification

router = APIRouter()


class LoanCreate(BaseModel):
    kind: LoanKindEnum = LoanKindEnum.ADVANCE
    principal: float = Field(..., gt=0)
    reason: str = Field(..., min_length=5, max_length=2000)
    instalment_count: int = Field(1, ge=1, le=36)
    # HR may raise one on someone's behalf; an employee may only ask for their own.
    employee_id: Optional[int] = None


class LoanDecision(BaseModel):
    approved: bool
    note: Optional[str] = Field(None, max_length=1000)
    # The approver may agree different terms from the ones asked for.
    principal: Optional[float] = Field(None, gt=0)
    instalment_count: Optional[int] = Field(None, ge=1, le=36)


class DisburseRequest(BaseModel):
    disbursed_on: Optional[date] = None
    # "YYYY-MM" of the first payslip to carry a deduction. Defaults to the
    # month after disbursement — recovering from a payslip that has already
    # been paid is not possible, and recovering in the same month usually
    # cancels out the point of the advance.
    first_recovery_month: Optional[str] = None

    @field_validator("first_recovery_month")
    @classmethod
    def _shape(cls, v):
        if v is None:
            return v
        parts = v.split("-")
        if len(parts) != 2 or len(parts[0]) != 4 or not (1 <= int(parts[1]) <= 12):
            raise ValueError("first_recovery_month must look like 2026-10")
        return f"{int(parts[0]):04d}-{int(parts[1]):02d}"


class WaiveRequest(BaseModel):
    reason: str = Field(..., min_length=3, max_length=1000)


class InstalmentOut(BaseModel):
    id: int
    sequence: int
    due_month: str
    amount: float
    status: InstalmentStatusEnum
    payroll_id: Optional[int] = None
    recovered_at: Optional[datetime] = None
    waived_reason: Optional[str] = None

    class Config:
        from_attributes = True


class LoanOut(BaseModel):
    id: int
    employee_id: int
    employee_name: Optional[str] = None
    kind: LoanKindEnum
    principal: float
    reason: str
    status: LoanStatusEnum
    requested_at: datetime
    approved_at: Optional[datetime] = None
    decision_note: Optional[str] = None
    disbursed_on: Optional[date] = None
    instalment_amount: Optional[float] = None
    instalment_count: Optional[int] = None
    first_recovery_month: Optional[str] = None
    settled_at: Optional[datetime] = None
    outstanding: float = 0.0
    recovered: float = 0.0
    instalments: List[InstalmentOut] = Field(default_factory=list)

    class Config:
        from_attributes = True


def _out(loan: EmployeeLoan) -> LoanOut:
    o = LoanOut.model_validate(loan, from_attributes=True)
    o.employee_name = loan.employee.full_name if loan.employee else None
    o.outstanding = loan.outstanding
    o.recovered = loan.recovered
    o.instalments = [InstalmentOut.model_validate(i, from_attributes=True)
                     for i in loan.instalments]
    return o


def _load(db: Session, loan_id: int) -> EmployeeLoan:
    loan = (db.query(EmployeeLoan)
            .options(joinedload(EmployeeLoan.instalments),
                     joinedload(EmployeeLoan.employee))
            .filter(EmployeeLoan.id == loan_id).first())
    if not loan:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Loan not found")
    return loan


@router.post("", response_model=LoanOut, status_code=status.HTTP_201_CREATED)
def create_loan(payload: LoanCreate, request: Request, db: Session = Depends(get_db),
                current_user: User = Depends(get_current_user)):
    target_id = payload.employee_id or current_user.id
    if target_id != current_user.id and not is_privileged(current_user, db):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="You can only request an advance for yourself")
    employee = db.query(User).filter(User.id == target_id).first()
    if not employee:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Employee not found")

    loan = EmployeeLoan(
        employee_id=employee.id, kind=payload.kind, principal=round(payload.principal, 2),
        reason=payload.reason, instalment_count=payload.instalment_count,
        instalment_amount=round(payload.principal / payload.instalment_count, 2),
        requested_by=current_user.id,
        status=LoanStatusEnum.REQUESTED)
    db.add(loan)
    db.commit()
    db.refresh(loan)

    for approver in db.query(User).filter(User.is_active.is_(True)).all():
        if is_privileged(approver) and approver.id != current_user.id:
            create_notification(
                db, approver.id, f"{employee.full_name} requested a salary {payload.kind.value}",
                f"PKR {payload.principal:,.0f} over {payload.instalment_count} "
                f"instalment{'s' if payload.instalment_count != 1 else ''}.",
                NotificationTypeEnum.INFO, related_id=loan.id)
    audit.record(db, "loan.requested", actor=current_user, request=request,
                 target_type="loan", target_id=loan.id,
                 detail={"employee_id": employee.id, "principal": loan.principal,
                         "kind": payload.kind.value})
    return _out(loan)


@router.get("", response_model=List[LoanOut])
def list_loans(status_filter: Optional[LoanStatusEnum] = Query(None, alias="status"),
               employee_id: Optional[int] = None,
               db: Session = Depends(get_db),
               current_user: User = Depends(require_admin_or_hr)):
    q = db.query(EmployeeLoan).options(joinedload(EmployeeLoan.instalments),
                                       joinedload(EmployeeLoan.employee))
    if status_filter:
        q = q.filter(EmployeeLoan.status == status_filter)
    if employee_id:
        q = q.filter(EmployeeLoan.employee_id == employee_id)
    return [_out(loan) for loan in q.order_by(EmployeeLoan.requested_at.desc()).all()]


@router.get("/me", response_model=List[LoanOut])
def my_loans(db: Session = Depends(get_db),
             current_user: User = Depends(get_current_user)):
    loans = (db.query(EmployeeLoan)
             .options(joinedload(EmployeeLoan.instalments), joinedload(EmployeeLoan.employee))
             .filter(EmployeeLoan.employee_id == current_user.id)
             .order_by(EmployeeLoan.requested_at.desc()).all())
    return [_out(loan) for loan in loans]


@router.get("/outstanding/{employee_id}")
def employee_outstanding(employee_id: int, db: Session = Depends(get_db),
                         current_user: User = Depends(require_admin_or_hr)):
    """One number, for the profile header and the final settlement."""
    return {"employee_id": employee_id, "outstanding": outstanding_for(db, employee_id)}


@router.get("/{loan_id}", response_model=LoanOut)
def get_loan(loan_id: int, db: Session = Depends(get_db),
             current_user: User = Depends(get_current_user)):
    loan = _load(db, loan_id)
    if loan.employee_id != current_user.id and not is_privileged(current_user, db):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not allowed")
    return _out(loan)


@router.put("/{loan_id}/decide", response_model=LoanOut)
def decide_loan(loan_id: int, payload: LoanDecision, request: Request,
                db: Session = Depends(get_db),
                current_user: User = Depends(require_admin_or_hr)):
    loan = _load(db, loan_id)
    if loan.status != LoanStatusEnum.REQUESTED:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail=f"This request has already been {loan.status.value}")
    if loan.employee_id == current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="You cannot approve your own advance")

    loan.decision_note = payload.note
    loan.approved_by = current_user.id
    loan.approved_at = datetime.utcnow()
    if payload.approved:
        if payload.principal:
            loan.principal = round(payload.principal, 2)
        if payload.instalment_count:
            loan.instalment_count = payload.instalment_count
        loan.instalment_amount = round(loan.principal / (loan.instalment_count or 1), 2)
        loan.status = LoanStatusEnum.APPROVED
    else:
        loan.status = LoanStatusEnum.REJECTED
    db.commit()
    db.refresh(loan)

    create_notification(
        db, loan.employee_id,
        f"Salary {loan.kind.value} {loan.status.value}",
        payload.note or (f"PKR {loan.principal:,.0f} approved over {loan.instalment_count} "
                         f"instalments." if payload.approved else "Your request was declined."),
        NotificationTypeEnum.SUCCESS if payload.approved else NotificationTypeEnum.WARNING,
        related_id=loan.id)
    audit.record(db, "loan.decided", actor=current_user, request=request,
                 target_type="loan", target_id=loan.id,
                 detail={"approved": payload.approved, "principal": loan.principal})
    return _out(loan)


@router.post("/{loan_id}/disburse", response_model=LoanOut)
def disburse_loan(loan_id: int, payload: DisburseRequest, request: Request,
                  db: Session = Depends(get_db),
                  current_user: User = Depends(require_admin_or_hr)):
    """
    Record the money as paid and lay down the recovery schedule.

    Deliberately separate from approval: an approved advance that was never
    actually paid must not start deducting from payslips.
    """
    loan = _load(db, loan_id)
    if loan.status != LoanStatusEnum.APPROVED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only an approved request can be disbursed "
                   f"(this one is {loan.status.value})")

    disbursed = payload.disbursed_on or date.today()
    first = payload.first_recovery_month
    if not first:
        nxt = disbursed.month + 1
        first = month_key(disbursed.year + (1 if nxt > 12 else 0), 1 if nxt > 12 else nxt)

    loan.disbursed_on = disbursed
    loan.first_recovery_month = first
    loan.status = LoanStatusEnum.DISBURSED
    loan.instalment_amount = round(loan.principal / (loan.instalment_count or 1), 2)
    db.flush()
    for inst in build_schedule(loan):
        db.add(inst)
    db.commit()
    db.refresh(loan)

    create_notification(
        db, loan.employee_id, "Advance disbursed",
        f"PKR {loan.principal:,.0f} has been released. Recovery starts on the "
        f"{first} payslip, {loan.instalment_count} instalment"
        f"{'s' if loan.instalment_count != 1 else ''} of "
        f"PKR {loan.instalment_amount:,.0f}.",
        NotificationTypeEnum.INFO, related_id=loan.id)
    audit.record(db, "loan.disbursed", actor=current_user, request=request,
                 target_type="loan", target_id=loan.id,
                 detail={"disbursed_on": str(disbursed), "first_recovery_month": first})
    return _out(loan)


@router.put("/{loan_id}/instalments/{instalment_id}/waive", response_model=LoanOut)
def waive_instalment(loan_id: int, instalment_id: int, payload: WaiveRequest,
                     request: Request, db: Session = Depends(get_db),
                     current_user: User = Depends(require_admin_or_hr)):
    """Write off one instalment. The reason is required — this is company money."""
    loan = _load(db, loan_id)
    inst = next((i for i in loan.instalments if i.id == instalment_id), None)
    if not inst:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Instalment not found")
    if inst.status == InstalmentStatusEnum.RECOVERED:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="That instalment has already been deducted from a payslip")
    inst.status = InstalmentStatusEnum.WAIVED
    inst.waived_by = current_user.id
    inst.waived_reason = payload.reason
    refresh_settlement(db, loan)
    db.commit()
    db.refresh(loan)
    audit.record(db, "loan.instalment_waived", actor=current_user, request=request,
                 target_type="loan", target_id=loan.id,
                 detail={"instalment_id": instalment_id, "amount": inst.amount,
                         "reason": payload.reason})
    return _out(loan)


@router.delete("/{loan_id}", status_code=status.HTTP_204_NO_CONTENT)
def cancel_loan(loan_id: int, request: Request, db: Session = Depends(get_db),
                current_user: User = Depends(get_current_user)):
    """
    Withdraw a request before it is paid out. Anything already disbursed stays
    on the record — the money moved.
    """
    loan = _load(db, loan_id)
    if loan.employee_id != current_user.id and not is_privileged(current_user, db):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not allowed")
    if loan.status == LoanStatusEnum.DISBURSED:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="This has already been paid out and cannot be cancelled")
    loan.status = LoanStatusEnum.CANCELLED
    db.commit()
    audit.record(db, "loan.cancelled", actor=current_user, request=request,
                 target_type="loan", target_id=loan.id)
