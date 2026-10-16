"""
Final settlement: what is owed on the last day.

The draft is computed; the approval is human. Nothing is finalised on the
computation alone, and an exit cannot be completed until a settlement has been
approved — which is the point of the whole module. Before this existed, an
employee's account was closed and their exit pack mailed while the last payment
was still an unopened spreadsheet.
"""
from datetime import date, datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session, joinedload

from app.api.deps import get_current_user, is_privileged, require_admin_or_hr
from app.config import settings
from app.database import get_db
from app.models.lifecycle import Resignation, ResignationStatusEnum
from app.models.settlement import FinalSettlement, SettlementStatusEnum
from app.models.user import User
from app.utils import audit
from app.utils.settlement import compute, recalculate_totals

router = APIRouter()

# Lines HR may override by hand. Editing one records it in `manual_fields`, so
# a later recompute leaves it alone.
EDITABLE = {
    "final_month_amount", "leave_encashment_days", "leave_encashment_amount",
    "gratuity_amount", "pending_reimbursements", "other_earning",
    "other_earning_note", "notice_shortfall_amount", "loan_outstanding",
    "asset_recovery", "tax_deduction", "other_deduction",
    "other_deduction_note", "notes",
}
# Notes and labels are not figures — editing them does not pin a computed line.
NON_FIGURE = {"other_earning_note", "other_deduction_note", "notes"}


class SettlementUpdate(BaseModel):
    final_month_amount: Optional[float] = Field(None, ge=0)
    leave_encashment_days: Optional[float] = Field(None, ge=0)
    leave_encashment_amount: Optional[float] = Field(None, ge=0)
    gratuity_amount: Optional[float] = Field(None, ge=0)
    pending_reimbursements: Optional[float] = Field(None, ge=0)
    other_earning: Optional[float] = Field(None, ge=0)
    other_earning_note: Optional[str] = Field(None, max_length=200)
    notice_shortfall_amount: Optional[float] = Field(None, ge=0)
    loan_outstanding: Optional[float] = Field(None, ge=0)
    asset_recovery: Optional[float] = Field(None, ge=0)
    tax_deduction: Optional[float] = Field(None, ge=0)
    other_deduction: Optional[float] = Field(None, ge=0)
    other_deduction_note: Optional[str] = Field(None, max_length=200)
    notes: Optional[str] = None


class MarkPaid(BaseModel):
    payment_reference: Optional[str] = Field(None, max_length=120)
    paid_on: Optional[date] = None


class SettlementLine(BaseModel):
    key: str
    label: str
    amount: float
    detail: Optional[str] = None


class SettlementOut(BaseModel):
    id: int
    resignation_id: int
    employee_id: int
    employee_name: Optional[str] = None
    designation: Optional[str] = None
    status: SettlementStatusEnum
    last_working_day: date
    computed_at: datetime
    monthly_gross: float

    final_month_days: float
    final_month_total_days: float
    final_month_amount: float
    leave_encashment_days: float
    leave_encashment_amount: float
    gratuity_years: float
    gratuity_amount: float
    pending_reimbursements: float
    other_earning: float
    other_earning_note: Optional[str] = None

    notice_shortfall_days: float
    notice_shortfall_amount: float
    loan_outstanding: float
    asset_recovery: float
    tax_deduction: float
    other_deduction: float
    other_deduction_note: Optional[str] = None

    gross_payable: float
    total_deductions: float
    net_payable: float
    manual_fields: List[str] = Field(default_factory=list)
    notes: Optional[str] = None
    approved_at: Optional[datetime] = None
    paid_at: Optional[datetime] = None
    payment_reference: Optional[str] = None

    # Rendered lines, so the frontend prints the statement the way it prints a
    # payslip instead of hard-coding which fields exist.
    earnings: List[SettlementLine] = Field(default_factory=list)
    deductions: List[SettlementLine] = Field(default_factory=list)
    policy: dict = Field(default_factory=dict)

    class Config:
        from_attributes = True


def _lines(s: FinalSettlement) -> tuple:
    earnings = [
        SettlementLine(
            key="final_month", label="Salary for the final month",
            amount=round(s.final_month_amount or 0.0, 2),
            detail=(f"{s.final_month_days:g} of {s.final_month_total_days:g} working days"
                    if s.final_month_total_days else None)),
        SettlementLine(
            key="leave_encashment", label="Unused annual leave encashed",
            amount=round(s.leave_encashment_amount or 0.0, 2),
            detail=(f"{s.leave_encashment_days:g} day"
                    f"{'s' if s.leave_encashment_days != 1 else ''}"
                    if s.leave_encashment_days else None)),
        SettlementLine(
            key="gratuity", label="Gratuity",
            amount=round(s.gratuity_amount or 0.0, 2),
            detail=(f"{s.gratuity_years:g} year"
                    f"{'s' if s.gratuity_years != 1 else ''} of service"
                    if s.gratuity_years else None)),
        SettlementLine(key="reimbursements", label="Approved expense claims outstanding",
                       amount=round(s.pending_reimbursements or 0.0, 2)),
        SettlementLine(key="other_earning", label=s.other_earning_note or "Other",
                       amount=round(s.other_earning or 0.0, 2)),
    ]
    deductions = [
        SettlementLine(
            key="notice_shortfall", label="Notice not served",
            amount=round(s.notice_shortfall_amount or 0.0, 2),
            detail=(f"{s.notice_shortfall_days:g} day"
                    f"{'s' if s.notice_shortfall_days != 1 else ''}"
                    if s.notice_shortfall_days else None)),
        SettlementLine(key="loan_outstanding", label="Advance or loan outstanding",
                       amount=round(s.loan_outstanding or 0.0, 2)),
        SettlementLine(key="asset_recovery", label="Equipment recovery",
                       amount=round(s.asset_recovery or 0.0, 2)),
        SettlementLine(key="tax", label="Income tax",
                       amount=round(s.tax_deduction or 0.0, 2)),
        SettlementLine(key="other_deduction", label=s.other_deduction_note or "Other",
                       amount=round(s.other_deduction or 0.0, 2)),
    ]
    # A statement full of zero rows reads as an error. Keep only what applies.
    return ([e for e in earnings if e.amount],
            [d for d in deductions if d.amount])


def _out(s: FinalSettlement) -> SettlementOut:
    o = SettlementOut.model_validate(s, from_attributes=True)
    o.employee_name = s.employee.full_name if s.employee else None
    o.designation = s.employee.designation if s.employee else None
    o.manual_fields = list(s.manual_fields or [])
    o.earnings, o.deductions = _lines(s)
    # The policy in force when this was computed, stated on the settlement
    # itself — someone reading it a year later should not have to guess which
    # rules produced these numbers.
    o.policy = {
        "gratuity_enabled": settings.GRATUITY_ENABLED,
        "gratuity_days_per_year": settings.GRATUITY_DAYS_PER_YEAR,
        "encash_annual_carryover": settings.ENCASH_ANNUAL_CARRYOVER,
        "leave_encashment_basis": settings.LEAVE_ENCASHMENT_BASIS,
        "recover_notice_shortfall": settings.RECOVER_NOTICE_SHORTFALL,
        "currency": settings.CURRENCY,
    }
    return o


def _load(db: Session, settlement_id: int) -> FinalSettlement:
    s = (db.query(FinalSettlement)
         .options(joinedload(FinalSettlement.employee))
         .filter(FinalSettlement.id == settlement_id).first())
    if not s:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="Settlement not found")
    return s


@router.post("/resignation/{resignation_id}", response_model=SettlementOut,
             status_code=status.HTTP_201_CREATED)
def draft_settlement(resignation_id: int, request: Request,
                     db: Session = Depends(get_db),
                     current_user: User = Depends(require_admin_or_hr)):
    """
    Compute the draft, or refresh an existing one.

    Refreshing keeps every line HR has already edited by hand and updates only
    the rest — so picking up a late expense claim does not undo a figure
    somebody negotiated.
    """
    r = (db.query(Resignation).options(joinedload(Resignation.employee))
         .filter(Resignation.id == resignation_id).first())
    if not r:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="Resignation not found")
    if r.status in (ResignationStatusEnum.REJECTED, ResignationStatusEnum.WITHDRAWN):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail=f"This resignation was {r.status.value}")

    existing = (db.query(FinalSettlement)
                .filter(FinalSettlement.resignation_id == resignation_id).first())
    if existing and existing.status == SettlementStatusEnum.PAID:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="This settlement has already been paid")
    if existing and existing.status == SettlementStatusEnum.APPROVED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This settlement is approved. Reopen it before recomputing.")

    s = compute(db, r, existing=existing, actor_id=current_user.id)
    if not existing:
        db.add(s)
    db.commit()
    db.refresh(s)
    audit.record(db, "settlement.drafted", actor=current_user, request=request,
                 target_type="settlement", target_id=s.id,
                 detail={"employee_id": s.employee_id, "net_payable": s.net_payable})
    return _out(s)


@router.get("", response_model=List[SettlementOut])
def list_settlements(status_filter: Optional[SettlementStatusEnum] = None,
                     db: Session = Depends(get_db),
                     current_user: User = Depends(require_admin_or_hr)):
    q = db.query(FinalSettlement).options(joinedload(FinalSettlement.employee))
    if status_filter:
        q = q.filter(FinalSettlement.status == status_filter)
    return [_out(s) for s in q.order_by(FinalSettlement.last_working_day.desc()).all()]


@router.get("/me", response_model=Optional[SettlementOut])
def my_settlement(db: Session = Depends(get_db),
                  current_user: User = Depends(get_current_user)):
    """
    My own settlement — visible only once it is approved.

    A draft is a working figure that may still move; showing it would set an
    expectation the company has not yet agreed to.
    """
    s = (db.query(FinalSettlement)
         .options(joinedload(FinalSettlement.employee))
         .filter(FinalSettlement.employee_id == current_user.id,
                 FinalSettlement.status != SettlementStatusEnum.DRAFT)
         .order_by(FinalSettlement.id.desc()).first())
    return _out(s) if s else None


@router.get("/resignation/{resignation_id}", response_model=Optional[SettlementOut])
def settlement_for_resignation(resignation_id: int, db: Session = Depends(get_db),
                               current_user: User = Depends(require_admin_or_hr)):
    s = (db.query(FinalSettlement)
         .options(joinedload(FinalSettlement.employee))
         .filter(FinalSettlement.resignation_id == resignation_id).first())
    return _out(s) if s else None


@router.get("/{settlement_id}", response_model=SettlementOut)
def get_settlement(settlement_id: int, db: Session = Depends(get_db),
                   current_user: User = Depends(get_current_user)):
    s = _load(db, settlement_id)
    if s.employee_id != current_user.id and not is_privileged(current_user, db):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not allowed")
    if s.employee_id == current_user.id and s.status == SettlementStatusEnum.DRAFT:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="This settlement has not been approved yet")
    return _out(s)


@router.put("/{settlement_id}", response_model=SettlementOut)
def update_settlement(settlement_id: int, payload: SettlementUpdate, request: Request,
                      db: Session = Depends(get_db),
                      current_user: User = Depends(require_admin_or_hr)):
    """Override a line. Only a draft is editable."""
    s = _load(db, settlement_id)
    if s.status != SettlementStatusEnum.DRAFT:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail=f"A {s.status.value} settlement cannot be edited")
    changes = payload.model_dump(exclude_unset=True)
    manual = set(s.manual_fields or [])
    for field, value in changes.items():
        if field not in EDITABLE:
            continue
        setattr(s, field, value)
        if field not in NON_FIGURE:
            manual.add(field)
    s.manual_fields = sorted(manual)
    recalculate_totals(s)
    db.commit()
    db.refresh(s)
    audit.record(db, "settlement.updated", actor=current_user, request=request,
                 target_type="settlement", target_id=s.id, detail=changes)
    return _out(s)


@router.post("/{settlement_id}/approve", response_model=SettlementOut)
def approve_settlement(settlement_id: int, request: Request,
                       db: Session = Depends(get_db),
                       current_user: User = Depends(require_admin_or_hr)):
    """
    Sign the figures off. The exit can be finalised once this is done.

    Nobody approves their own: an HR manager leaving the company does not get
    to settle their own final payment.
    """
    s = _load(db, settlement_id)
    if s.status != SettlementStatusEnum.DRAFT:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail=f"Already {s.status.value}")
    if s.employee_id == current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="You cannot approve your own final settlement")
    if s.net_payable < 0:
        # Legitimate — someone can owe the company on the way out — but it
        # should be a deliberate decision, so it has to be noted first.
        if not (s.notes or "").strip():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="This settlement is negative. Add a note explaining how "
                       "the balance will be recovered before approving.")
    s.status = SettlementStatusEnum.APPROVED
    s.approved_by = current_user.id
    s.approved_at = datetime.utcnow()
    db.commit()
    db.refresh(s)
    audit.record(db, "settlement.approved", actor=current_user, request=request,
                 target_type="settlement", target_id=s.id,
                 detail={"net_payable": s.net_payable, "employee_id": s.employee_id})
    return _out(s)


@router.post("/{settlement_id}/reopen", response_model=SettlementOut)
def reopen_settlement(settlement_id: int, request: Request,
                      db: Session = Depends(get_db),
                      current_user: User = Depends(require_admin_or_hr)):
    """Put an approved settlement back to draft. Not possible once paid."""
    s = _load(db, settlement_id)
    if s.status == SettlementStatusEnum.PAID:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="This settlement has been paid and cannot be reopened")
    if s.status == SettlementStatusEnum.DRAFT:
        return _out(s)
    s.status = SettlementStatusEnum.DRAFT
    s.approved_by = None
    s.approved_at = None
    db.commit()
    db.refresh(s)
    audit.record(db, "settlement.reopened", actor=current_user, request=request,
                 target_type="settlement", target_id=s.id)
    return _out(s)


@router.post("/{settlement_id}/mark-paid", response_model=SettlementOut)
def mark_paid(settlement_id: int, payload: MarkPaid, request: Request,
              db: Session = Depends(get_db),
              current_user: User = Depends(require_admin_or_hr)):
    s = _load(db, settlement_id)
    if s.status != SettlementStatusEnum.APPROVED:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Approve the settlement before recording payment")
    s.status = SettlementStatusEnum.PAID
    s.paid_at = (datetime.combine(payload.paid_on, datetime.min.time())
                 if payload.paid_on else datetime.utcnow())
    s.payment_reference = payload.payment_reference
    db.commit()
    db.refresh(s)
    audit.record(db, "settlement.paid", actor=current_user, request=request,
                 target_type="settlement", target_id=s.id,
                 detail={"reference": payload.payment_reference,
                         "net_payable": s.net_payable})
    return _out(s)
