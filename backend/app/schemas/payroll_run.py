import re
from pydantic import BaseModel, Field, field_validator
from typing import Optional, List
from datetime import datetime
from app.models.payroll_run import PayrollRunStatusEnum


# ---- Runs ----

class PayrollRunCreate(BaseModel):
    month: str = Field(..., description='Payroll period as "YYYY-MM", e.g. "2025-11"')

    @field_validator("month")
    @classmethod
    def validate_month(cls, v: str) -> str:
        if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", v or ""):
            raise ValueError('month must be in "YYYY-MM" format, e.g. "2025-11"')
        return v


class PayrollRunSummary(BaseModel):
    """List view — totals computed on the fly, never stored."""
    id: int
    month: str
    status: PayrollRunStatusEnum
    created_at: datetime
    finalized_at: Optional[datetime] = None
    employee_count: int
    total_gross: float
    total_net: float


class PayrollRunReopen(BaseModel):
    reason: str = Field(..., min_length=1, description="Why the finalized run is being reopened (audited)")


# ---- Entries ----

class PayrollEntryAdd(BaseModel):
    employee_id: int


class PayrollEntryUpdate(BaseModel):
    """Adjustable fields only. monthly_salary is a snapshot and is NOT updatable
    here — it must never be rewritten from the employee record."""
    increment: Optional[float] = None
    allowance_overtime: Optional[float] = None
    arrears: Optional[float] = None
    bonus: Optional[float] = None
    tax_deduction: Optional[float] = None
    other_deduction: Optional[float] = None
    notes: Optional[str] = None


class PayrollEntryResponse(BaseModel):
    id: int
    payroll_run_id: int
    employee_id: int
    employee_name: str
    designation: Optional[str] = None

    monthly_salary: float
    increment: float
    allowance_overtime: float
    arrears: float
    bonus: float
    tax_deduction: float
    other_deduction: float
    notes: Optional[str] = None

    # Computed server-side from the hybrid properties.
    gross_salary: float
    gross_total: float
    net_payable: float

    updated_at: Optional[datetime] = None


class PayrollRunDetail(BaseModel):
    id: int
    month: str
    status: PayrollRunStatusEnum
    created_by: int
    created_at: datetime
    finalized_at: Optional[datetime] = None
    entries: List[PayrollEntryResponse]
    total_gross: float
    total_net: float


# ---- Audit ----

class PayrollAuditLogResponse(BaseModel):
    id: int
    # Null for run-level events (finalize/reopen), which carry payroll_run_id
    # instead — so this must stay optional or those rows fail to serialize.
    payroll_entry_id: Optional[int] = None
    payroll_run_id: Optional[int] = None
    field_changed: str
    old_value: Optional[float] = None
    new_value: Optional[float] = None
    note: Optional[str] = None
    changed_by: int
    changed_at: datetime

    class Config:
        from_attributes = True
