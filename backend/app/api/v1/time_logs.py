"""
Manually logged time: forgotten check-ins, and overtime claimed for pay.

Both are self-reported, so both carry two signatures — the line manager who
saw the work, then HR/admin who pays for it. An employee with no line manager
(a director, or anyone reporting straight to an admin) goes to the HR stage
directly rather than waiting in a queue nobody owns.
"""
from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session, joinedload

from app.api.deps import get_current_user, is_privileged
from app.database import get_db
from app.models.attendance import Attendance
from app.models.overtime import OvertimeRecord, OvertimeStatusEnum
from app.models.time_log import TimeLogRequest, TimeLogKindEnum, TimeLogStatusEnum
from app.models.user import User
from app.schemas.time_log import (TimeLogCreate, TimeLogDecision, TimeLogResponse,
                                  TimeLogSuggestion)
from app.utils import audit
from app.utils.notifications import create_notification
from app.models.notification import NotificationTypeEnum
from app.utils.overtime import hourly_rate_from_salary, calculate_flat_overtime_pay

router = APIRouter()

OPEN_STATUSES = (TimeLogStatusEnum.PENDING, TimeLogStatusEnum.MANAGER_APPROVED)


# ---------------------------------------------------------------------------
# Who signs next
# ---------------------------------------------------------------------------

def _has_line_manager(db: Session, employee: User) -> bool:
    if not employee.manager_id:
        return False
    mgr = db.query(User).filter(User.id == employee.manager_id,
                                User.is_active == True).first()  # noqa: E712
    return mgr is not None


def _initial_status(db: Session, employee: User) -> TimeLogStatusEnum:
    """Skip the manager stage when there is no manager to skip to."""
    return (TimeLogStatusEnum.PENDING if _has_line_manager(db, employee)
            else TimeLogStatusEnum.MANAGER_APPROVED)


def _can_decide(db: Session, req: TimeLogRequest, actor: User) -> bool:
    """
    Whether this actor is the one the request is currently waiting on.

    An employee never signs off their own claim, even if they are the manager
    or an admin — two signatures on one person's own request is one signature.
    """
    if req.employee_id == actor.id:
        return False
    if req.status == TimeLogStatusEnum.PENDING:
        employee = req.employee or db.query(User).filter(User.id == req.employee_id).first()
        return bool(employee and employee.manager_id == actor.id)
    if req.status == TimeLogStatusEnum.MANAGER_APPROVED:
        return is_privileged(actor, db)
    return False


def _next_approver(db: Session, req: TimeLogRequest) -> Optional[str]:
    if req.status == TimeLogStatusEnum.PENDING:
        return "line manager"
    if req.status == TimeLogStatusEnum.MANAGER_APPROVED:
        return "HR / admin"
    return None


def _to_response(db: Session, req: TimeLogRequest, viewer: User) -> TimeLogResponse:
    resp = TimeLogResponse.model_validate(req)
    resp.employee_name = req.employee.full_name if req.employee else None
    resp.manager_approver_name = req.manager_approver.full_name if req.manager_approver else None
    resp.approver_name = req.approver.full_name if req.approver else None
    resp.can_approve = _can_decide(db, req, viewer)
    resp.next_approver = _next_approver(db, req)
    return resp


# ---------------------------------------------------------------------------
# Applying an approved request
# ---------------------------------------------------------------------------

def _apply_attendance(db: Session, req: TimeLogRequest) -> None:
    """Write the approved session into attendance."""
    if req.applied_attendance_id:
        return
    row = Attendance(
        employee_id=req.employee_id,
        check_in_time=req.check_in_time,
        check_out_time=req.check_out_time,
        duration_hours=req.hours,
        notes=f"Manually logged and approved. {req.reason}"[:500],
    )
    db.add(row)
    db.flush()
    req.applied_attendance_id = row.id


def _apply_overtime(db: Session, req: TimeLogRequest) -> None:
    """
    Roll every approved overtime claim into the payroll period that will pay
    it, which is what payroll reads.

    The period is not simply the month the work happened. Payroll is prepared
    on the 25th, so a claim the employee filed after that has missed the run
    and belongs to the next month — see payroll_period_for(). The deadline is
    the employee's: an approver sitting on a claim for a fortnight must not
    move somebody else's money into next month. Where the period it earned has
    since been paid out, open_payroll_period() carries it to the next one that
    can still change, so it is paid late rather than lost.

    Every period the employee has approved claims in is recomputed, not just
    this request's: an approval that lands on the 26th moves that claim out of
    the month it was sitting in, and the month it left has to be re-totalled or
    it keeps paying for work it no longer holds. Recomputed from the claims
    rather than incremented, so a later rejection or correction cannot leave a
    total overstated.
    """
    from app.utils.overtime import open_payroll_period, payroll_period_for

    def period_of(claim):
        filed = claim.created_at or claim.work_date
        return open_payroll_period(db, claim.employee_id,
                                   *payroll_period_for(claim.work_date, filed))

    claims = db.query(TimeLogRequest).filter(
        TimeLogRequest.employee_id == req.employee_id,
        TimeLogRequest.kind == TimeLogKindEnum.OVERTIME,
        TimeLogRequest.status == TimeLogStatusEnum.APPROVED,
    ).all()

    by_period: dict = {}
    for claim in claims:
        by_period.setdefault(period_of(claim), []).append(claim)

    # This request's own period is included even when it holds no claims, so a
    # rejection that empties a period zeroes it instead of leaving it standing.
    by_period.setdefault(period_of(req), [])

    for (month, year), period_claims in by_period.items():
        record = db.query(OvertimeRecord).filter(
            OvertimeRecord.employee_id == req.employee_id,
            OvertimeRecord.month == month,
            OvertimeRecord.year == year,
        ).first()
        if not record:
            if not period_claims:
                continue
            record = OvertimeRecord(employee_id=req.employee_id,
                                    month=month, year=year)
            db.add(record)

        record.overtime_hours = round(sum(c.hours or 0.0 for c in period_claims), 2)
        record.overtime_pay = round(sum(c.amount or 0.0 for c in period_claims), 2)
        record.pay_is_manual = True
        record.status = OvertimeStatusEnum.APPROVED
        record.approved_by = req.approved_by
        record.approved_at = req.approved_at


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/suggest", response_model=TimeLogSuggestion)
def suggest_amount(hours: float, db: Session = Depends(get_db),
                   current_user: User = Depends(get_current_user)):
    """
    The pro-rata figure for a given number of hours.

    Not used by the logging form — an employee does not enter or see an amount
    while claiming. This exists for approver screens that want to show the
    rate behind a claim.
    """
    if hours <= 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="hours must be greater than zero")
    return TimeLogSuggestion(
        hours=hours,
        hourly_rate=round(hourly_rate_from_salary(
            current_user.base_salary,
            standard_hours_per_day=current_user.standard_hours), 2),
        suggested_amount=round(calculate_flat_overtime_pay(
            current_user.base_salary, hours,
            standard_hours_per_day=current_user.standard_hours), 2),
    )


@router.post("/", response_model=TimeLogResponse, status_code=status.HTTP_201_CREATED)
def create_time_log(payload: TimeLogCreate, request: Request,
                    db: Session = Depends(get_db),
                    current_user: User = Depends(get_current_user)):
    """File a manual attendance or overtime claim against yourself."""
    if payload.work_date > datetime.utcnow() + timedelta(days=1):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Time cannot be logged for a future date.")

    if payload.kind == TimeLogKindEnum.ATTENDANCE:
        hours = round((payload.check_out_time - payload.check_in_time).total_seconds() / 3600, 2)
        # A claimed session overlapping one already on record would double-count
        # the day, so it is refused rather than silently merged.
        clash = db.query(Attendance).filter(
            Attendance.employee_id == current_user.id,
            Attendance.check_in_time < payload.check_out_time,
            Attendance.check_out_time > payload.check_in_time,
        ).first()
        if clash:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(f"You already have attendance from "
                        f"{clash.check_in_time:%d %b %H:%M} that overlaps this."),
            )
        amount = suggested = None
    else:
        hours = payload.hours
        # Pro rata from the claimant's own monthly salary over a 160-hour
        # month, so 1.5 hours pays exactly 1.5 hours' worth. Computed here
        # rather than sent by the client: the figure follows from the hours and
        # the salary, and neither is the claimant's to assert.
        suggested = round(calculate_flat_overtime_pay(
            current_user.base_salary, hours,
            standard_hours_per_day=current_user.standard_hours), 2)
        amount = suggested

    req = TimeLogRequest(
        employee_id=current_user.id, kind=payload.kind, work_date=payload.work_date,
        check_in_time=payload.check_in_time, check_out_time=payload.check_out_time,
        hours=hours, amount=amount, suggested_amount=suggested,
        reason=payload.reason, status=_initial_status(db, current_user),
    )
    db.add(req)
    db.commit()
    db.refresh(req)

    audit.record(db, "time_log.created", actor=current_user, request=request,
                 target_type="time_log", target_id=req.id,
                 detail={"kind": payload.kind.value, "hours": hours, "amount": amount})
    return _to_response(db, req, current_user)


@router.get("/me", response_model=List[TimeLogResponse])
def my_time_logs(db: Session = Depends(get_db),
                 current_user: User = Depends(get_current_user)):
    rows = (db.query(TimeLogRequest)
            .options(joinedload(TimeLogRequest.employee))
            .filter(TimeLogRequest.employee_id == current_user.id)
            .order_by(TimeLogRequest.work_date.desc()).all())
    return [_to_response(db, r, current_user) for r in rows]


@router.get("/pending", response_model=List[TimeLogResponse])
def pending_for_me(db: Session = Depends(get_db),
                   current_user: User = Depends(get_current_user)):
    """
    Exactly the requests waiting on this caller — a manager sees their own
    reports awaiting the first signature, HR sees everything awaiting the
    second. Nobody sees a queue they cannot act on.
    """
    rows = (db.query(TimeLogRequest)
            .options(joinedload(TimeLogRequest.employee))
            .filter(TimeLogRequest.status.in_(OPEN_STATUSES))
            .order_by(TimeLogRequest.work_date.desc()).all())
    return [_to_response(db, r, current_user) for r in rows
            if _can_decide(db, r, current_user)]


@router.get("/employee/{employee_id}", response_model=List[TimeLogResponse])
def employee_time_logs(employee_id: int, db: Session = Depends(get_db),
                       current_user: User = Depends(get_current_user)):
    employee = db.query(User).filter(User.id == employee_id).first()
    if not employee:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Employee not found")
    if not (current_user.id == employee_id or is_privileged(current_user, db)
            or employee.manager_id == current_user.id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not enough permissions")
    rows = (db.query(TimeLogRequest)
            .options(joinedload(TimeLogRequest.employee))
            .filter(TimeLogRequest.employee_id == employee_id)
            .order_by(TimeLogRequest.work_date.desc()).all())
    return [_to_response(db, r, current_user) for r in rows]


@router.put("/{log_id}/decide", response_model=TimeLogResponse)
def decide_time_log(log_id: int, decision: TimeLogDecision, request: Request,
                    db: Session = Depends(get_db),
                    current_user: User = Depends(get_current_user)):
    """
    Sign off the stage this request is currently at.

    One endpoint for both signatures: which stage the caller is completing is
    decided by the request's own status and who is asking, so the client can
    never advance the wrong one.
    """
    req = (db.query(TimeLogRequest)
           .options(joinedload(TimeLogRequest.employee))
           .filter(TimeLogRequest.id == log_id).first())
    if not req:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Request not found")
    if req.status not in OPEN_STATUSES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail=f"This request is already {req.status.value}.")
    if not _can_decide(db, req, current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=("You cannot decide your own request."
                    if req.employee_id == current_user.id
                    else f"This request is waiting on the {_next_approver(db, req)}."),
        )

    now = datetime.utcnow()
    if not decision.approved:
        req.status = TimeLogStatusEnum.REJECTED
        req.rejected_by = current_user.id
        req.rejection_reason = decision.rejection_reason
        outcome = "rejected"
    elif req.status == TimeLogStatusEnum.PENDING:
        req.status = TimeLogStatusEnum.MANAGER_APPROVED
        req.manager_approved_by, req.manager_approved_at = current_user.id, now
        outcome = "manager_approved"
    else:
        req.manager_approved_by = req.manager_approved_by or current_user.id
        req.manager_approved_at = req.manager_approved_at or now
        req.status = TimeLogStatusEnum.APPROVED
        req.approved_by, req.approved_at = current_user.id, now
        outcome = "approved"

    # An approver may correct the figure as they sign it.
    if decision.amount is not None and req.kind == TimeLogKindEnum.OVERTIME:
        req.amount = decision.amount

    if req.status == TimeLogStatusEnum.APPROVED:
        db.flush()
        if req.kind == TimeLogKindEnum.ATTENDANCE:
            _apply_attendance(db, req)
        else:
            _apply_overtime(db, req)

    db.commit()
    db.refresh(req)

    try:
        create_notification(
            db=db, user_id=req.employee_id,
            title=f"{req.kind.value.title()} log {outcome.replace('_', ' ')}",
            message=(f"Your {req.kind.value} log for {req.work_date:%d %b %Y} was "
                     f"{outcome.replace('_', ' ')} by {current_user.full_name}."
                     + (f" Reason: {req.rejection_reason}" if req.rejection_reason else "")),
            notification_type=(NotificationTypeEnum.SUCCESS if decision.approved
                               else NotificationTypeEnum.WARNING),
        )
        db.commit()
    except Exception:
        db.rollback()

    audit.record(db, f"time_log.{outcome}", actor=current_user, request=request,
                 target_type="time_log", target_id=req.id,
                 detail={"employee_id": req.employee_id, "kind": req.kind.value,
                         "hours": req.hours, "amount": req.amount})
    return _to_response(db, req, current_user)


@router.delete("/{log_id}", status_code=status.HTTP_204_NO_CONTENT)
def cancel_time_log(log_id: int, db: Session = Depends(get_db),
                    current_user: User = Depends(get_current_user)):
    """Withdraw your own request while it is still open."""
    req = db.query(TimeLogRequest).filter(TimeLogRequest.id == log_id).first()
    if not req or req.employee_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Request not found")
    if req.status not in OPEN_STATUSES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail=f"This request is already {req.status.value}.")
    req.status = TimeLogStatusEnum.CANCELLED
    db.commit()
    return None
