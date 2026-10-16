"""
Ending probation on purpose.

A decision here is a manager's or HR's, never automatic: nobody is confirmed by
a date passing, which is exactly what used to happen — silently, with no record
and no letter.
"""
from datetime import date, datetime, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, is_privileged, require_admin_or_hr
from app.database import get_db
from app.models.notification import NotificationTypeEnum
from app.models.probation import (ProbationDecisionEnum, ProbationReview,
                                  ProbationStatusEnum)
from app.models.user import User
from app.utils import audit
from app.utils.notifications import create_notification
from app.utils.probation import (current_status, due_for_review,
                                 effective_end_date, issue_confirmation_letter)

router = APIRouter()


class ProbationDecisionIn(BaseModel):
    decision: ProbationDecisionEnum
    comments: Optional[str] = Field(None, max_length=2000)
    # Required when extending; ignored otherwise.
    new_end_date: Optional[date] = None
    # Confirmation takes effect on the probation end date unless stated.
    effective_date: Optional[date] = None
    issue_letter: bool = True


class ProbationSetup(BaseModel):
    probation_end_date: Optional[date] = None
    probation_status: Optional[ProbationStatusEnum] = None


class ReviewOut(BaseModel):
    id: int
    employee_id: int
    period_end: date
    decision: ProbationDecisionEnum
    new_end_date: Optional[date] = None
    comments: Optional[str] = None
    decided_at: datetime
    decided_by: int
    reviewer_name: Optional[str] = None
    document_id: Optional[int] = None

    class Config:
        from_attributes = True


class ProbationOut(BaseModel):
    employee_id: int
    employee_name: str
    designation: Optional[str] = None
    department: Optional[str] = None
    date_of_joining: Optional[datetime] = None
    manager_id: Optional[int] = None
    manager_name: Optional[str] = None
    probation_end_date: Optional[date] = None
    status: str
    days_remaining: Optional[int] = None
    is_overdue: bool = False
    can_decide: bool = False
    reviews: List[ReviewOut] = Field(default_factory=list)


def _reviews(db: Session, employee_id: int) -> List[ReviewOut]:
    rows = (db.query(ProbationReview)
            .filter(ProbationReview.employee_id == employee_id)
            .order_by(ProbationReview.decided_at.desc()).all())
    out = []
    for r in rows:
        item = ReviewOut.model_validate(r, from_attributes=True)
        item.reviewer_name = r.reviewer.full_name if r.reviewer else None
        out.append(item)
    return out


def _out(db: Session, employee: User, viewer: User,
         with_reviews: bool = False) -> ProbationOut:
    end = effective_end_date(employee)
    manager = (db.query(User).filter(User.id == employee.manager_id).first()
               if employee.manager_id else None)
    status_value = current_status(employee)
    o = ProbationOut(
        employee_id=employee.id, employee_name=employee.full_name,
        designation=employee.designation, department=employee.department,
        date_of_joining=employee.date_of_joining, manager_id=employee.manager_id,
        manager_name=manager.full_name if manager else None,
        probation_end_date=end, status=status_value)
    if end:
        o.days_remaining = (end - date.today()).days
        o.is_overdue = (o.days_remaining < 0 and status_value in (
            ProbationStatusEnum.IN_PROBATION.value, ProbationStatusEnum.EXTENDED.value))
    # The line manager decides; HR and admins can decide for anyone, including
    # where no manager is set.
    o.can_decide = (is_privileged(viewer, db) or employee.manager_id == viewer.id) \
        and employee.id != viewer.id
    if with_reviews:
        o.reviews = _reviews(db, employee.id)
    return o


@router.get("/due", response_model=List[ProbationOut])
def probation_due(within_days: int = 14, db: Session = Depends(get_db),
                  current_user: User = Depends(get_current_user)):
    """
    Decisions waiting to be made. A manager sees their own team; HR sees all.

    Includes anyone already past their end date without a decision — the
    quietly-overdue case is the one worth surfacing.
    """
    people = due_for_review(db, within_days=within_days)
    if not is_privileged(current_user, db):
        people = [p for p in people if p.manager_id == current_user.id]
    return [_out(db, p, current_user) for p in people]


@router.get("/me", response_model=ProbationOut)
def my_probation(db: Session = Depends(get_db),
                 current_user: User = Depends(get_current_user)):
    return _out(db, current_user, current_user, with_reviews=True)


@router.get("/{employee_id}", response_model=ProbationOut)
def employee_probation(employee_id: int, db: Session = Depends(get_db),
                       current_user: User = Depends(get_current_user)):
    employee = db.query(User).filter(User.id == employee_id).first()
    if not employee:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Employee not found")
    if not (employee_id == current_user.id or employee.manager_id == current_user.id
            or is_privileged(current_user, db)):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not allowed")
    return _out(db, employee, current_user, with_reviews=True)


@router.put("/{employee_id}/setup", response_model=ProbationOut)
def set_probation(employee_id: int, payload: ProbationSetup, request: Request,
                  db: Session = Depends(get_db),
                  current_user: User = Depends(require_admin_or_hr)):
    """
    Set or correct the end date, or mark that probation does not apply.

    Separate from deciding an outcome: fixing a date somebody typed wrong is
    not the same act as confirming an employee.
    """
    employee = db.query(User).filter(User.id == employee_id).first()
    if not employee:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Employee not found")
    changes = payload.model_dump(exclude_unset=True)
    if "probation_end_date" in changes:
        employee.probation_end_date = changes["probation_end_date"]
        # A moved deadline is a new deadline; the manager should hear about it.
        employee.probation_notice_sent_at = None
    if "probation_status" in changes and changes["probation_status"] is not None:
        employee.probation_status = changes["probation_status"].value
    db.commit()
    db.refresh(employee)
    audit.record(db, "probation.setup", actor=current_user, request=request,
                 target_type="user", target_id=employee.id,
                 detail={k: str(v) for k, v in changes.items()})
    return _out(db, employee, current_user, with_reviews=True)


class BulkConfirm(BaseModel):
    # Everyone whose probation window closed before this date. Defaults to a
    # year ago: somebody the company has employed for a year past their
    # probation is confirmed in every sense except the paperwork.
    ended_before: Optional[date] = None
    employee_ids: Optional[List[int]] = None
    mark_not_applicable: Optional[List[int]] = None
    note: Optional[str] = Field(None, max_length=500)


@router.post("/bulk-confirm", response_model=List[ProbationOut])
def bulk_confirm(payload: BulkConfirm, request: Request,
                 db: Session = Depends(get_db),
                 current_user: User = Depends(require_admin_or_hr)):
    """
    Clear the historical backlog in one action.

    Switching this feature on flags every employee at once, because nobody has
    ever been formally confirmed — the CEO included. Those are not decisions
    anybody needs to make one at a time in 2026. This records them as
    confirmed, with a review row for each so the audit trail says who did it
    and when, and deliberately issues no letters: a confirmation letter dated
    today for someone confirmed in practice years ago would be a fiction.

    Anyone still genuinely inside their probation is left alone, whatever
    dates are passed.
    """
    cutoff = payload.ended_before or (date.today() - timedelta(days=365))
    not_applicable = set(payload.mark_not_applicable or [])

    candidates = []
    for employee in db.query(User).filter(User.is_active.is_(True)).all():
        if payload.employee_ids and employee.id not in payload.employee_ids \
                and employee.id not in not_applicable:
            continue
        if current_status(employee) not in (ProbationStatusEnum.IN_PROBATION.value,
                                            ProbationStatusEnum.EXTENDED.value):
            continue
        end = effective_end_date(employee)
        if employee.id in not_applicable:
            candidates.append((employee, True))
        elif end and end < cutoff:
            candidates.append((employee, False))

    for employee, exempt in candidates:
        if exempt:
            employee.probation_status = ProbationStatusEnum.NOT_APPLICABLE.value
            continue
        employee.probation_status = ProbationStatusEnum.CONFIRMED.value
        db.add(ProbationReview(
            employee_id=employee.id,
            period_end=effective_end_date(employee) or date.today(),
            decision=ProbationDecisionEnum.CONFIRM,
            comments=payload.note or
            "Recorded in bulk: employed well beyond the probation period, "
            "confirmed in practice. No letter issued.",
            decided_by=current_user.id))
        employee.probation_notice_sent_at = datetime.utcnow()

    db.commit()
    audit.record(db, "probation.bulk_confirmed", actor=current_user, request=request,
                 target_type="user",
                 detail={"count": len(candidates), "ended_before": str(cutoff)})
    return [_out(db, e, current_user) for e, _ in candidates]


@router.post("/{employee_id}/decide", response_model=ProbationOut)
def decide_probation(employee_id: int, payload: ProbationDecisionIn, request: Request,
                     db: Session = Depends(get_db),
                     current_user: User = Depends(get_current_user)):
    employee = db.query(User).filter(User.id == employee_id).first()
    if not employee:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Employee not found")
    if employee.id == current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="You cannot decide your own probation")
    if not (is_privileged(current_user, db) or employee.manager_id == current_user.id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="Only the line manager, HR or an administrator can decide")
    if current_status(employee) == ProbationStatusEnum.CONFIRMED.value:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="This employee is already confirmed")

    period_end = effective_end_date(employee) or date.today()
    review = ProbationReview(
        employee_id=employee.id, period_end=period_end,
        decision=payload.decision, comments=payload.comments,
        decided_by=current_user.id)

    if payload.decision == ProbationDecisionEnum.EXTEND:
        if not payload.new_end_date:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                                detail="An extension needs a new end date")
        if payload.new_end_date <= period_end:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                                detail="The new end date must be after the current one")
        review.new_end_date = payload.new_end_date
        employee.probation_end_date = payload.new_end_date
        employee.probation_status = ProbationStatusEnum.EXTENDED.value
        # New period, new deadline: the manager gets reminded again.
        employee.probation_notice_sent_at = None
        title, body = ("Probation extended",
                       f"Your probation has been extended to "
                       f"{payload.new_end_date.strftime('%d %B %Y')}.")
        tone = NotificationTypeEnum.WARNING

    elif payload.decision == ProbationDecisionEnum.CONFIRM:
        effective = payload.effective_date or period_end
        employee.probation_status = ProbationStatusEnum.CONFIRMED.value
        employee.probation_end_date = effective
        db.flush()
        if payload.issue_letter:
            doc = issue_confirmation_letter(db, employee, current_user, effective)
            review.document_id = doc.id
        title, body = ("Your employment is confirmed",
                       f"You have completed your probationary period with effect from "
                       f"{effective.strftime('%d %B %Y')}."
                       + (" Your confirmation letter is in your documents."
                          if payload.issue_letter else ""))
        tone = NotificationTypeEnum.SUCCESS

    else:  # NOT_CONFIRMED
        # The status stays as it is. Ending someone's employment is a separate
        # act with its own approvals; this records the decision, nothing more.
        title, body = ("Probation review recorded",
                       "Your line manager has recorded a probation review. "
                       "HR will be in touch.")
        tone = NotificationTypeEnum.WARNING

    db.add(review)
    db.commit()
    db.refresh(employee)

    create_notification(db, employee.id, title, body, tone, related_id=employee.id)
    audit.record(db, "probation.decided", actor=current_user, request=request,
                 target_type="user", target_id=employee.id,
                 detail={"decision": payload.decision.value,
                         "new_end_date": str(payload.new_end_date or ""),
                         # Whether a letter was asked for. Five confirmations
                         # went through with none issued and nothing recorded
                         # said why, which is what this is here to stop.
                         "issue_letter": payload.issue_letter,
                         "document_id": review.document_id})
    return _out(db, employee, current_user, with_reviews=True)
