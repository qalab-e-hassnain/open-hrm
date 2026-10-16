from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime
from app.models.overtime import OvertimeStatusEnum


class OvertimeComputeRequest(BaseModel):
    """Admin: (re)compute overtime records for a pay period from attendance."""
    month: int = Field(..., ge=1, le=12)
    year: int = Field(..., ge=2000)
    employee_id: Optional[int] = None  # None = all employees with overtime that period


class OvertimeApproval(BaseModel):
    """Admin: approve or reject a pending overtime record."""
    status: OvertimeStatusEnum  # APPROVED or REJECTED


class OvertimeResponse(BaseModel):
    id: int
    employee_id: int
    employee_name: Optional[str] = None
    month: int
    year: int
    overtime_hours: float
    overtime_pay: float
    # Derived, not stored: the employee's current base salary / 160, and what
    # that rate pays for these hours. `overtime_pay` stays 0 until the record is
    # approved and absorbed by a payroll, so this is what the admin approves on.
    hourly_rate: Optional[float] = None
    estimated_pay: Optional[float] = None
    status: OvertimeStatusEnum
    approved_by: Optional[int] = None
    payroll_id: Optional[int] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
