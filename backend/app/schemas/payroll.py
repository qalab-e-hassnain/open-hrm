from pydantic import BaseModel, Field

from app.schemas.datetimes import NaiveUTC
from typing import Optional, List
from datetime import datetime
from app.models.payroll import PayrollStatusEnum


# Base Payroll Schema
class PayrollComponentsMixin(BaseModel):
    """Optional itemisation of the allowance and deduction totals.

    Supplying these is what turns a lump "Allowances: 20,000" into the named
    lines a payslip has to show. The server reconciles them against the totals
    (see _sync_payroll_components), so a caller may send either, or both.
    """
    house_allowance: float = Field(default=0.0, ge=0)
    transport_allowance: float = Field(default=0.0, ge=0)
    medical_allowance: float = Field(default=0.0, ge=0)
    other_allowance: float = Field(default=0.0, ge=0)
    other_allowance_note: Optional[str] = None

    provident_fund: float = Field(default=0.0, ge=0)
    social_security: float = Field(default=0.0, ge=0)
    eobi: float = Field(default=0.0, ge=0)
    loan_repayment: float = Field(default=0.0, ge=0)
    loan_repayment_note: Optional[str] = None
    advance_recovery: float = Field(default=0.0, ge=0)
    advance_recovery_note: Optional[str] = None
    other_deduction: float = Field(default=0.0, ge=0)
    other_deduction_note: Optional[str] = None


class PayrollBase(PayrollComponentsMixin):
    base_salary: float = Field(..., gt=0)
    allowances: float = Field(default=0.0, ge=0)
    bonuses: float = Field(default=0.0, ge=0)
    arrears: float = Field(default=0.0, ge=0)
    deductions: float = Field(default=0.0, ge=0)
    unapproved_leave_fine: float = Field(default=0.0, ge=0, multiple_of=5000)
    payment_method: Optional[str] = None
    payment_reference: Optional[str] = None
    # Server-computed from salary slabs unless pinned. Supplying it on an
    # update pins it (tax_is_manual), which is how pension-scheme exemptions
    # the slab engine cannot see are reflected.
    tax: float = Field(default=0.0, ge=0)
    month: int = Field(..., ge=1, le=12)
    year: int = Field(..., ge=2000)
    notes: Optional[str] = None
    # Backfilled record for a period predating the system; trusts the tax above.
    is_historical: bool = False


# Payroll Create Schema
class PayrollCreate(PayrollBase):
    employee_id: int
    # When true, also save this payslip's base_salary/allowances as the
    # employee's new standing salary (an increment) — used for later payslips.
    apply_increment: bool = False


# Payroll Update Schema
class PayrollUpdate(BaseModel):
    base_salary: Optional[float] = Field(None, gt=0)
    allowances: Optional[float] = Field(None, ge=0)
    bonuses: Optional[float] = Field(None, ge=0)
    arrears: Optional[float] = Field(None, ge=0)
    deductions: Optional[float] = Field(None, ge=0)
    unapproved_leave_fine: Optional[float] = Field(None, ge=0, multiple_of=5000)
    tax: Optional[float] = Field(None, ge=0)
    status: Optional[PayrollStatusEnum] = None
    payment_date: Optional[NaiveUTC] = None
    payment_method: Optional[str] = None
    payment_reference: Optional[str] = None
    notes: Optional[str] = None
    is_historical: Optional[bool] = None
    # Pin the tax against recomputation, or set false to hand it
    # back to the slab engine.
    tax_is_manual: Optional[bool] = None

    # Itemisation — all optional so a partial update can adjust one line.
    house_allowance: Optional[float] = Field(None, ge=0)
    transport_allowance: Optional[float] = Field(None, ge=0)
    medical_allowance: Optional[float] = Field(None, ge=0)
    other_allowance: Optional[float] = Field(None, ge=0)
    other_allowance_note: Optional[str] = None
    provident_fund: Optional[float] = Field(None, ge=0)
    social_security: Optional[float] = Field(None, ge=0)
    eobi: Optional[float] = Field(None, ge=0)
    loan_repayment: Optional[float] = Field(None, ge=0)
    loan_repayment_note: Optional[str] = None
    advance_recovery: Optional[float] = Field(None, ge=0)
    advance_recovery_note: Optional[str] = None
    other_deduction: Optional[float] = Field(None, ge=0)
    other_deduction_note: Optional[str] = None


# Payroll Response Schema
class PayrollResponse(PayrollBase):
    id: int
    employee_id: int
    # True when `tax` was set by hand (or carried from a payroll run) and is
    # therefore protected from recomputation.
    tax_is_manual: bool = False
    reimbursement_total: float = 0.0
    overtime_pay: float = 0.0
    unpaid_leave_deduction: float = 0.0
    net_salary: float
    status: PayrollStatusEnum
    payment_date: Optional[NaiveUTC] = None
    payslip_no: Optional[str] = None
    version: int = 1
    generated_at: Optional[datetime] = None
    correction_reason: Optional[str] = None
    cancellation_reason: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# ----- Tax Certificate (annual salary tax statement, PK Jul-Jun tax year) -----

class TaxCertificateRow(BaseModel):
    month: int          # calendar month 1-12
    year: int
    month_label: str    # e.g. "Jul 2025"
    total_income: float
    exempt_income: float
    taxable_income: float
    tax_paid: float
    cumulative_tax: float


class TaxCertificateTotals(BaseModel):
    total_income: float
    exempt_income: float
    taxable_income: float
    tax_paid: float


class TaxCertificateCompany(BaseModel):
    name: str
    reg_no: str
    ntn: str
    address: str
    email: Optional[str] = None


class TaxCertificateEmployee(BaseModel):
    name: str
    cnic: Optional[str] = None
    employee_id: Optional[str] = None
    designation: Optional[str] = None


class TaxCertificateResponse(BaseModel):
    company: TaxCertificateCompany
    employee: TaxCertificateEmployee
    period_label: str   # e.g. "Aug 2025 - Dec 2025"
    currency: str
    rows: List[TaxCertificateRow]
    totals: TaxCertificateTotals

