"""
Shift patterns and who is on them.
"""
from datetime import date, datetime, time
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import or_
from sqlalchemy.orm import Session, joinedload

from app.api.deps import get_current_user, is_privileged, require_admin_or_hr
from app.database import get_db
from app.models.shift import Shift, ShiftAssignment
from app.models.user import User
from app.utils import audit
from app.utils.shifts import forget_assignments, shift_for

router = APIRouter()

DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
             "Saturday", "Sunday"]


class ShiftIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)
    start_time: time
    end_time: time
    break_minutes: int = Field(0, ge=0, le=480)
    days_of_week: str = Field("1111100", min_length=7, max_length=7)
    grace_minutes: int = Field(15, ge=0, le=240)
    is_active: bool = True
    is_default: bool = False

    @field_validator("days_of_week")
    @classmethod
    def _binary(cls, v):
        if set(v) - {"0", "1"}:
            raise ValueError("days_of_week is seven characters of 0 or 1, Monday first")
        return v


class ShiftUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=80)
    start_time: Optional[time] = None
    end_time: Optional[time] = None
    break_minutes: Optional[int] = Field(None, ge=0, le=480)
    days_of_week: Optional[str] = Field(None, min_length=7, max_length=7)
    grace_minutes: Optional[int] = Field(None, ge=0, le=240)
    is_active: Optional[bool] = None
    is_default: Optional[bool] = None


class AssignmentIn(BaseModel):
    employee_id: int
    shift_id: int
    effective_from: Optional[date] = None


class ShiftOut(BaseModel):
    id: int
    name: str
    start_time: time
    end_time: time
    break_minutes: int
    days_of_week: str
    grace_minutes: int
    is_active: bool
    is_default: bool
    hours: float = 0.0
    working_days: List[str] = Field(default_factory=list)
    assigned_count: int = 0

    class Config:
        from_attributes = True


class AssignmentOut(BaseModel):
    id: int
    employee_id: int
    employee_name: Optional[str] = None
    shift_id: int
    shift_name: Optional[str] = None
    effective_from: date
    effective_to: Optional[date] = None

    class Config:
        from_attributes = True


def _out(db: Session, shift: Shift) -> ShiftOut:
    o = ShiftOut.model_validate(shift, from_attributes=True)
    o.hours = shift.hours
    o.working_days = [DAY_NAMES[i] for i, flag in enumerate(shift.days_of_week or "")
                      if flag == "1" and i < 7]
    o.assigned_count = (db.query(ShiftAssignment)
                        .filter(ShiftAssignment.shift_id == shift.id,
                                or_(ShiftAssignment.effective_to.is_(None),
                                    ShiftAssignment.effective_to >= date.today()))
                        .count())
    return o


def _assignment_out(a: ShiftAssignment) -> AssignmentOut:
    o = AssignmentOut.model_validate(a, from_attributes=True)
    o.employee_name = a.employee.full_name if a.employee else None
    o.shift_name = a.shift.name if a.shift else None
    return o


@router.get("", response_model=List[ShiftOut])
def list_shifts(include_inactive: bool = False, db: Session = Depends(get_db),
                current_user: User = Depends(get_current_user)):
    q = db.query(Shift)
    if not include_inactive:
        q = q.filter(Shift.is_active.is_(True))
    return [_out(db, s) for s in q.order_by(Shift.start_time, Shift.name).all()]


@router.post("", response_model=ShiftOut, status_code=status.HTTP_201_CREATED)
def create_shift(payload: ShiftIn, request: Request, db: Session = Depends(get_db),
                 current_user: User = Depends(require_admin_or_hr)):
    if db.query(Shift).filter(Shift.name == payload.name).first():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail=f"A shift called '{payload.name}' already exists")
    shift = Shift(**payload.model_dump())
    if shift.is_default:
        # Exactly one default, or "the default shift" means nothing.
        db.query(Shift).update({"is_default": False})
    db.add(shift)
    db.commit()
    db.refresh(shift)
    audit.record(db, "shift.created", actor=current_user, request=request,
                 target_type="shift", target_id=shift.id, detail={"name": shift.name})
    return _out(db, shift)


@router.put("/{shift_id}", response_model=ShiftOut)
def update_shift(shift_id: int, payload: ShiftUpdate, request: Request,
                 db: Session = Depends(get_db),
                 current_user: User = Depends(require_admin_or_hr)):
    shift = db.query(Shift).filter(Shift.id == shift_id).first()
    if not shift:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Shift not found")
    changes = payload.model_dump(exclude_unset=True)
    if changes.get("is_default"):
        db.query(Shift).filter(Shift.id != shift_id).update({"is_default": False})
    for field, value in changes.items():
        setattr(shift, field, value)
    db.commit()
    db.refresh(shift)
    audit.record(db, "shift.updated", actor=current_user, request=request,
                 target_type="shift", target_id=shift.id,
                 detail={k: str(v) for k, v in changes.items()})
    return _out(db, shift)


@router.delete("/{shift_id}", status_code=status.HTTP_204_NO_CONTENT)
def deactivate_shift(shift_id: int, request: Request, db: Session = Depends(get_db),
                     current_user: User = Depends(require_admin_or_hr)):
    """
    Retire a pattern. Deactivated rather than deleted — past attendance was
    judged against it, and that judgement has to stay explicable.
    """
    shift = db.query(Shift).filter(Shift.id == shift_id).first()
    if not shift:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Shift not found")
    shift.is_active = False
    shift.is_default = False
    db.commit()
    audit.record(db, "shift.deactivated", actor=current_user, request=request,
                 target_type="shift", target_id=shift_id)


@router.get("/assignments", response_model=List[AssignmentOut])
def list_assignments(current_only: bool = True, db: Session = Depends(get_db),
                     current_user: User = Depends(require_admin_or_hr)):
    q = (db.query(ShiftAssignment)
         .options(joinedload(ShiftAssignment.employee), joinedload(ShiftAssignment.shift)))
    if current_only:
        q = q.filter(or_(ShiftAssignment.effective_to.is_(None),
                         ShiftAssignment.effective_to >= date.today()))
    return [_assignment_out(a)
            for a in q.order_by(ShiftAssignment.effective_from.desc()).all()]


@router.post("/assignments", response_model=AssignmentOut,
             status_code=status.HTTP_201_CREATED)
def assign_shift(payload: AssignmentIn, request: Request, db: Session = Depends(get_db),
                 current_user: User = Depends(require_admin_or_hr)):
    """
    Put someone on a shift from a given date.

    Any assignment already running is closed the day before, so an employee is
    never on two patterns at once and last month's attendance is still judged
    against last month's shift.
    """
    employee = db.query(User).filter(User.id == payload.employee_id).first()
    if not employee:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Employee not found")
    shift = db.query(Shift).filter(Shift.id == payload.shift_id).first()
    if not shift or not shift.is_active:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="Shift not found, or no longer active")

    start = payload.effective_from or date.today()
    from datetime import timedelta
    open_rows = (db.query(ShiftAssignment)
                 .filter(ShiftAssignment.employee_id == employee.id,
                         or_(ShiftAssignment.effective_to.is_(None),
                             ShiftAssignment.effective_to >= start))
                 .all())
    for row in open_rows:
        if row.effective_from >= start:
            db.delete(row)          # a future assignment being replaced
        else:
            row.effective_to = start - timedelta(days=1)

    assignment = ShiftAssignment(employee_id=employee.id, shift_id=shift.id,
                                 effective_from=start, assigned_by=current_user.id)
    db.add(assignment)
    db.commit()
    db.refresh(assignment)
    forget_assignments(db, employee.id)   # the cached history is now stale
    audit.record(db, "shift.assigned", actor=current_user, request=request,
                 target_type="user", target_id=employee.id,
                 detail={"shift": shift.name, "from": str(start)})
    return _assignment_out(assignment)


@router.get("/me", response_model=Optional[ShiftOut])
def my_shift(on: Optional[date] = None, db: Session = Depends(get_db),
             current_user: User = Depends(get_current_user)):
    shift = shift_for(db, current_user.id, on)
    return _out(db, shift) if shift else None


@router.get("/employee/{employee_id}", response_model=Optional[ShiftOut])
def employee_shift(employee_id: int, on: Optional[date] = None,
                   db: Session = Depends(get_db),
                   current_user: User = Depends(get_current_user)):
    employee = db.query(User).filter(User.id == employee_id).first()
    if not employee:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Employee not found")
    if not (employee_id == current_user.id or employee.manager_id == current_user.id
            or is_privileged(current_user, db)):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not allowed")
    shift = shift_for(db, employee_id, on)
    return _out(db, shift) if shift else None
