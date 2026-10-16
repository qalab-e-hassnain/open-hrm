from datetime import datetime
from typing import Optional, List

from pydantic import BaseModel, Field, model_validator

from app.schemas.datetimes import NaiveUTC

from app.models.time_log import TimeLogKindEnum, TimeLogStatusEnum


class TimeLogCreate(BaseModel):
    kind: TimeLogKindEnum
    work_date: NaiveUTC
    reason: str = Field(..., min_length=3, max_length=1000)

    # Attendance: the session that was never clocked.
    check_in_time: Optional[NaiveUTC] = None
    check_out_time: Optional[NaiveUTC] = None

    # Overtime: hours only. The amount is derived pro rata from the employee's
    # own salary and is deliberately not accepted here — a claimant naming
    # their own figure is the thing the two signatures exist to prevent, and
    # it would make every claim an argument about the rate rather than about
    # whether the hours were worked.
    hours: Optional[float] = Field(None, gt=0, le=24)

    @model_validator(mode="after")
    def check_shape(self):
        if self.kind == TimeLogKindEnum.ATTENDANCE:
            if not (self.check_in_time and self.check_out_time):
                raise ValueError("check_in_time and check_out_time are required for an attendance log")
            if self.check_out_time <= self.check_in_time:
                raise ValueError("check_out_time must be after check_in_time")
            span = (self.check_out_time - self.check_in_time).total_seconds() / 3600
            if span > 24:
                raise ValueError("a single session cannot exceed 24 hours")
        else:
            if not self.hours:
                raise ValueError("hours is required for an overtime log")
        return self


class TimeLogDecision(BaseModel):
    """Approve or reject. A rejection has to say why."""
    approved: bool
    # An approver may correct the paid amount as they sign it off.
    amount: Optional[float] = Field(None, ge=0)
    rejection_reason: Optional[str] = Field(None, max_length=1000)

    @model_validator(mode="after")
    def require_reason(self):
        if not self.approved and not (self.rejection_reason or "").strip():
            raise ValueError("rejection_reason is required when rejecting")
        return self


class TimeLogResponse(BaseModel):
    id: int
    employee_id: int
    employee_name: Optional[str] = None
    kind: TimeLogKindEnum
    work_date: NaiveUTC
    check_in_time: Optional[NaiveUTC] = None
    check_out_time: Optional[NaiveUTC] = None
    hours: float
    amount: Optional[float] = None
    suggested_amount: Optional[float] = None
    reason: str
    status: TimeLogStatusEnum

    manager_approved_by: Optional[int] = None
    manager_approver_name: Optional[str] = None
    manager_approved_at: Optional[datetime] = None
    approved_by: Optional[int] = None
    approver_name: Optional[str] = None
    approved_at: Optional[datetime] = None
    rejection_reason: Optional[str] = None

    # What this caller can do with it right now, so the UI does not have to
    # re-derive the rules that the server already enforces.
    can_approve: bool = False
    next_approver: Optional[str] = None

    created_at: datetime

    class Config:
        from_attributes = True


class TimeLogSuggestion(BaseModel):
    """What the formula would pay, offered as a starting figure."""
    hours: float
    hourly_rate: float
    suggested_amount: float
