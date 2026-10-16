"""
Resignation, clearance and exit.

Three signatures — line manager, an administrator, then HR clearance — and a
completion driven by the last working day rather than by the final approval, so
someone serving notice keeps their access until the day they actually leave.
"""
from datetime import date, datetime, timedelta
from typing import List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session, joinedload

from app.api.deps import get_current_user, is_privileged, require_admin, require_admin_or_hr
from app.utils.inv_movements import holdings_for
from app.config import settings
from app.database import get_db
from app.models.lifecycle import (Resignation, ResignationStatusEnum, ClearanceItem,
                                  TaskStatusEnum)
from app.models.user import User
from app.utils import audit
from app.utils.notifications import create_notification
from app.models.notification import NotificationTypeEnum

router = APIRouter()

OPEN = (ResignationStatusEnum.SUBMITTED, ResignationStatusEnum.MANAGER_APPROVED,
        ResignationStatusEnum.CEO_APPROVED, ResignationStatusEnum.CLEARED)

# The standing checklist every exit starts with. Specific equipment lines are
# added on top of these from the asset register, one per item the leaver holds.
DEFAULT_CLEARANCE = [
    ("assets", "Any equipment not on the register returned"),
    ("handover", "Work handed over to a named colleague"),
    ("handover", "Documentation and credentials transferred"),
    ("access", "Email and system accounts to be disabled"),
    ("access", "Repository and cloud access revoked"),
    ("finance", "No outstanding salary advance or loan"),
    ("finance", "Final expense claims submitted"),
]


class ResignationCreate(BaseModel):
    reason: str = Field(..., min_length=5, max_length=2000)
    # Defaults to the policy notice period; an employee may propose later.
    proposed_last_working_day: Optional[date] = None


class ApprovalDecision(BaseModel):
    approved: bool
    note: Optional[str] = Field(None, max_length=1000)
    # An administrator may agree a different last day — waiving or extending
    # notice is a real decision and belongs on the approval, not in a comment.
    agreed_last_working_day: Optional[date] = None
    rejection_reason: Optional[str] = Field(None, max_length=1000)


class ClearanceUpdate(BaseModel):
    status: TaskStatusEnum
    notes: Optional[str] = None
    reference: Optional[str] = None


class ClearanceOut(BaseModel):
    id: int
    category: str
    label: str
    reference: Optional[str] = None
    status: TaskStatusEnum
    notes: Optional[str] = None
    cleared_at: Optional[datetime] = None
    class Config:
        from_attributes = True


class ResignationOut(BaseModel):
    id: int
    employee_id: int
    employee_name: Optional[str] = None
    submitted_at: datetime
    reason: str
    notice_days: int
    proposed_last_working_day: date
    agreed_last_working_day: Optional[date] = None
    status: ResignationStatusEnum
    manager_note: Optional[str] = None
    ceo_note: Optional[str] = None
    rejection_reason: Optional[str] = None
    completed_at: Optional[datetime] = None
    exit_pack_sent_at: Optional[datetime] = None
    clearance_items: List[ClearanceOut] = Field(default_factory=list)
    clearance_outstanding: int = 0
    can_decide: bool = False
    next_approver: Optional[str] = None
    days_remaining: Optional[int] = None


def _next_approver(r: Resignation) -> Optional[str]:
    return {
        ResignationStatusEnum.SUBMITTED: "line manager",
        ResignationStatusEnum.MANAGER_APPROVED: "administrator",
        ResignationStatusEnum.CEO_APPROVED: "HR (clearance)",
    }.get(r.status)


def _can_decide(db: Session, r: Resignation, actor: User) -> bool:
    """
    Nobody signs off their own resignation, admin or not — the point of three
    signatures is three people.
    """
    if r.employee_id == actor.id or r.status not in OPEN:
        return False
    if r.status == ResignationStatusEnum.SUBMITTED:
        employee = r.employee or db.query(User).filter(User.id == r.employee_id).first()
        # With no line manager, the first signature falls to an administrator
        # rather than waiting in a queue nobody owns.
        if employee and employee.manager_id:
            return employee.manager_id == actor.id
        return actor.role.value == "admin" if hasattr(actor.role, "value") else False
    if r.status == ResignationStatusEnum.MANAGER_APPROVED:
        return (actor.role.value if hasattr(actor.role, "value") else str(actor.role)) == "admin"
    if r.status == ResignationStatusEnum.CEO_APPROVED:
        return is_privileged(actor, db)
    return False


def _out(db: Session, r: Resignation, viewer: User) -> ResignationOut:
    o = ResignationOut.model_validate(r, from_attributes=True)
    o.employee_name = r.employee.full_name if r.employee else None
    o.clearance_items = [ClearanceOut.model_validate(c) for c in
                         sorted(r.clearance_items, key=lambda x: (x.category, x.id))]
    o.clearance_outstanding = sum(1 for c in o.clearance_items
                                  if c.status == TaskStatusEnum.PENDING)
    o.can_decide = _can_decide(db, r, viewer)
    o.next_approver = _next_approver(r)
    last = r.agreed_last_working_day or r.proposed_last_working_day
    if last and r.status in OPEN:
        o.days_remaining = (last - date.today()).days
    return o


@router.post("/resignations", response_model=ResignationOut,
             status_code=status.HTTP_201_CREATED)
def submit_resignation(payload: ResignationCreate, request: Request,
                       db: Session = Depends(get_db),
                       current_user: User = Depends(get_current_user)):
    """Resign. The notice period sets the default last day; you may propose later."""
    existing = db.query(Resignation).filter(
        Resignation.employee_id == current_user.id,
        Resignation.status.in_(OPEN),
    ).first()
    if existing:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="You already have a resignation in progress.")

    notice = settings.NOTICE_PERIOD_DAYS
    earliest = date.today() + timedelta(days=notice)
    proposed = payload.proposed_last_working_day or earliest
    if proposed < date.today():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="The last working day cannot be in the past.")

    r = Resignation(employee_id=current_user.id, reason=payload.reason,
                    notice_days=notice, proposed_last_working_day=proposed)
    db.add(r); db.commit(); db.refresh(r)

    audit.record(db, "resignation.submitted", actor=current_user, request=request,
                 target_type="resignation", target_id=r.id,
                 detail={"proposed_last_working_day": str(proposed),
                         "serves_full_notice": proposed >= earliest})
    return _out(db, r, current_user)


@router.get("/resignations/me", response_model=Optional[ResignationOut])
def my_resignation(db: Session = Depends(get_db),
                   current_user: User = Depends(get_current_user)):
    r = (db.query(Resignation).options(joinedload(Resignation.employee))
         .filter(Resignation.employee_id == current_user.id)
         .order_by(Resignation.submitted_at.desc()).first())
    return _out(db, r, current_user) if r else None


@router.get("/resignations", response_model=List[ResignationOut])
def list_resignations(open_only: bool = True, db: Session = Depends(get_db),
                      current_user: User = Depends(get_current_user)):
    """
    Everything this caller can see: their own always, their reports' if they
    manage anyone, everything if privileged.
    """
    q = (db.query(Resignation).options(joinedload(Resignation.employee))
         .order_by(Resignation.submitted_at.desc()))
    if open_only:
        q = q.filter(Resignation.status.in_(OPEN))
    rows = q.all()
    if is_privileged(current_user, db):
        return [_out(db, r, current_user) for r in rows]
    reports = {u.id for u in db.query(User).filter(User.manager_id == current_user.id).all()}
    return [_out(db, r, current_user) for r in rows
            if r.employee_id == current_user.id or r.employee_id in reports]


@router.put("/resignations/{res_id}/decide", response_model=ResignationOut)
def decide(res_id: int, payload: ApprovalDecision, request: Request,
           db: Session = Depends(get_db),
           current_user: User = Depends(get_current_user)):
    """
    Sign off whichever stage this resignation is currently at. Which stage that
    is comes from the record and the caller, never from the client.
    """
    r = (db.query(Resignation).options(joinedload(Resignation.employee))
         .filter(Resignation.id == res_id).first())
    if not r:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Resignation not found")
    if not _can_decide(db, r, current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=("You cannot decide your own resignation."
                    if r.employee_id == current_user.id
                    else f"This is waiting on the {_next_approver(r) or 'nobody'}."))
    if not payload.approved and not (payload.rejection_reason or "").strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="A rejection needs a reason.")

    now = datetime.utcnow()
    if not payload.approved:
        r.status = ResignationStatusEnum.REJECTED
        r.rejected_by = current_user.id
        r.rejection_reason = payload.rejection_reason
        outcome = "rejected"
    elif r.status == ResignationStatusEnum.SUBMITTED:
        r.status = ResignationStatusEnum.MANAGER_APPROVED
        r.manager_approved_by, r.manager_approved_at = current_user.id, now
        r.manager_note = payload.note
        outcome = "manager_approved"
    elif r.status == ResignationStatusEnum.MANAGER_APPROVED:
        r.status = ResignationStatusEnum.CEO_APPROVED
        r.ceo_approved_by, r.ceo_approved_at = current_user.id, now
        r.ceo_note = payload.note
        if payload.agreed_last_working_day:
            r.agreed_last_working_day = payload.agreed_last_working_day
        r.agreed_last_working_day = r.agreed_last_working_day or r.proposed_last_working_day
        # Clearance is created here rather than at submission: there is nothing
        # for HR to work through until leaving is actually agreed.
        for category, label in DEFAULT_CLEARANCE:
            db.add(ClearanceItem(resignation_id=r.id, category=category, label=label))
        # One line per item this person actually holds, named and tagged. The
        # generic "laptop and charger returned" above is a checkbox with no
        # laptop attached to it; these are the real ones, and `reference`
        # carries the asset id exactly as ClearanceItem always intended.
        for item in holdings_for(db, r.employee_id):
            db.add(ClearanceItem(
                resignation_id=r.id, category="assets",
                label=f"Return {item.name} ({item.tag})",
                reference=str(item.id)))
        outcome = "ceo_approved"
    else:
        outstanding = [c for c in r.clearance_items if c.status == TaskStatusEnum.PENDING]
        if outstanding:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"{len(outstanding)} clearance item(s) are still open.")
        # A clearance item can be ticked by hand; the register cannot. If the
        # asset is still issued to them, it has not come back, whatever the
        # checkbox says.
        still_held = holdings_for(db, r.employee_id)
        if still_held:
            names = ", ".join(f"{i.name} ({i.tag})" for i in still_held)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Equipment is still issued to this employee: {names}. "
                       "Record the return, transfer it to another holder, or "
                       "have an admin write it off.")
        r.status = ResignationStatusEnum.CLEARED
        r.hr_cleared_by, r.hr_cleared_at = current_user.id, now
        outcome = "cleared"

    db.commit(); db.refresh(r)
    try:
        create_notification(
            db=db, user_id=r.employee_id,
            title=f"Resignation {outcome.replace('_', ' ')}",
            message=(f"Your resignation was {outcome.replace('_', ' ')} by "
                     f"{current_user.full_name}."
                     + (f" Reason: {r.rejection_reason}" if r.rejection_reason else "")),
            notification_type=(NotificationTypeEnum.INFO if payload.approved
                               else NotificationTypeEnum.WARNING))
        db.commit()
    except Exception:
        db.rollback()

    audit.record(db, f"resignation.{outcome}", actor=current_user, request=request,
                 target_type="resignation", target_id=r.id,
                 detail={"employee_id": r.employee_id,
                         "agreed_last_working_day": str(r.agreed_last_working_day or "")})
    return _out(db, r, current_user)


@router.put("/resignations/{res_id}/withdraw", response_model=ResignationOut)
def withdraw(res_id: int, request: Request, db: Session = Depends(get_db),
             current_user: User = Depends(get_current_user)):
    """Withdraw your own resignation while it is still open."""
    r = db.query(Resignation).filter(Resignation.id == res_id).first()
    if not r or r.employee_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Resignation not found")
    if r.status not in OPEN:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail=f"This resignation is already {r.status.value}.")
    r.status = ResignationStatusEnum.WITHDRAWN
    r.withdrawn_at = datetime.utcnow()
    db.commit(); db.refresh(r)
    audit.record(db, "resignation.withdrawn", actor=current_user, request=request,
                 target_type="resignation", target_id=r.id)
    return _out(db, r, current_user)


@router.put("/clearance/{item_id}", response_model=ResignationOut)
def update_clearance(item_id: int, payload: ClearanceUpdate, db: Session = Depends(get_db),
                     current_user: User = Depends(require_admin_or_hr)):
    item = db.query(ClearanceItem).filter(ClearanceItem.id == item_id).first()
    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Clearance item not found")
    item.status = payload.status
    item.notes = payload.notes if payload.notes is not None else item.notes
    item.reference = payload.reference if payload.reference is not None else item.reference
    item.cleared_by = current_user.id if payload.status != TaskStatusEnum.PENDING else None
    item.cleared_at = datetime.utcnow() if payload.status != TaskStatusEnum.PENDING else None
    db.commit()
    r = db.query(Resignation).filter(Resignation.id == item.resignation_id).first()
    return _out(db, r, current_user)


@router.post("/resignations/{res_id}/complete", response_model=ResignationOut)
def complete(res_id: int, background_tasks: BackgroundTasks, request: Request,
             db: Session = Depends(get_db),
             current_user: User = Depends(require_admin_or_hr)):
    """
    Close it out: issue the NOC, send the exit pack, deactivate the account.

    Kept manual as well as scheduled so HR can complete on the day rather than
    waiting for the overnight sweep.
    """
    r = (db.query(Resignation).options(joinedload(Resignation.employee))
         .filter(Resignation.id == res_id).first())
    if not r:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Resignation not found")
    if r.status != ResignationStatusEnum.CLEARED:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Clearance must be complete before an exit can be finalised.")
    from app.utils.offboarding import finalise_exit, settlement_ready

    # The account closes and the exit pack goes out in the next few lines. The
    # final payment has to be agreed before that, not after.
    ready, reason = settlement_ready(db, r)
    if not ready:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=reason)
    finalise_exit(db, r, actor=current_user, background_tasks=background_tasks)
    db.refresh(r)
    audit.record(db, "resignation.completed", actor=current_user, request=request,
                 target_type="resignation", target_id=r.id,
                 detail={"employee_id": r.employee_id})
    return _out(db, r, current_user)
