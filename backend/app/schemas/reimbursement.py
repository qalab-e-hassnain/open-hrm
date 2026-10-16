from pydantic import BaseModel, Field

from app.schemas.datetimes import NaiveUTC
from typing import Optional
from datetime import datetime
from app.models.reimbursement import ReimbursementTypeEnum, ReimbursementStatusEnum


# Base Reimbursement Schema
class ReimbursementBase(BaseModel):
    reimbursement_type: ReimbursementTypeEnum
    amount: float = Field(..., gt=0)
    description: str
    date_of_expense: NaiveUTC
    receipt_url: Optional[str] = None


# Reimbursement Create Schema
class ReimbursementCreate(ReimbursementBase):
    pass


# Reimbursement Update Schema
class ReimbursementUpdate(BaseModel):
    reimbursement_type: Optional[ReimbursementTypeEnum] = None
    amount: Optional[float] = Field(None, gt=0)
    description: Optional[str] = None
    date_of_expense: Optional[NaiveUTC] = None
    receipt_url: Optional[str] = None


# Reimbursement Response Schema
class ReimbursementResponse(ReimbursementBase):
    id: int
    employee_id: int
    employee_name: Optional[str] = None
    employee_email: Optional[str] = None
    status: ReimbursementStatusEnum
    approved_by: Optional[int] = None
    approver_name: Optional[str] = None
    approved_at: Optional[datetime] = None
    rejection_reason: Optional[str] = None
    paid_at: Optional[datetime] = None
    # How it was paid: "payroll" if a payslip carried it, "separate" if it was
    # handed over outside payroll, "" while it is still unpaid.
    paid_via: Optional[str] = None
    payroll_id: Optional[int] = None
    created_at: datetime
    updated_at: datetime
    
    class Config:
        from_attributes = True


# Reimbursement Approval Schema
class ReimbursementApproval(BaseModel):
    status: ReimbursementStatusEnum  # approved or rejected
    rejection_reason: Optional[str] = None

