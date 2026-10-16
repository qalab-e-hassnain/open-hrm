"""Schemas for the payslip document, its YTD figures and its dispute thread.

Kept apart from schemas/payroll.py, which describes the raw payroll record an
admin edits. What the employee sees is a *document*: named earning and
deduction lines, company and employee blocks, totals that reconcile, and the
identity needed to verify it. Assembling that server-side keeps one definition
of the payslip instead of one per client.
"""
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime

from app.models.payroll import PayrollStatusEnum
from app.models.payslip_dispute import DisputeCategoryEnum, DisputeStatusEnum


class PayslipLine(BaseModel):
    key: str
    label: str
    amount: float
    # Pakistani salary tax is charged on basic pay only; every other earning on
    # this payslip is exempt. Stated per line so the UI never has to guess.
    taxable: bool = False
    note: Optional[str] = None      # "Installment 2 of 4", "Laptop damage recovery"
    formula: Optional[str] = None   # "12h 30m x approved hourly rate"


class PayslipCompany(BaseModel):
    name: str
    # Same National Tax Number under both keys for now: `reg_no` is what the
    # payslip UI already reads, `ntn` is what the number actually is. Drop
    # reg_no once the frontend has moved over and relabelled the field.
    reg_no: str
    ntn: str
    address: str
    email: Optional[str] = None


class PayslipEmployee(BaseModel):
    name: str
    employee_id: Optional[str] = None
    department: Optional[str] = None
    designation: Optional[str] = None
    cnic: Optional[str] = None
    date_of_joining: Optional[datetime] = None
    # Masked at the source. The full account number is never serialised.
    bank_account_masked: Optional[str] = None
    bank_name: Optional[str] = None


class PayslipReconciliation(BaseModel):
    gross_earnings: float
    total_deductions: float
    expected_net: float
    net_salary: float
    balanced: bool


class PayslipRevision(BaseModel):
    id: int
    payslip_no: Optional[str] = None
    version: int
    status: PayrollStatusEnum
    generated_at: Optional[datetime] = None
    correction_reason: Optional[str] = None


class PayslipDetailResponse(BaseModel):
    id: int
    employee_id: int

    payslip_no: Optional[str] = None
    verification_code: Optional[str] = None
    version: int
    generated_at: Optional[datetime] = None
    status: PayrollStatusEnum
    is_historical: bool = False

    month: int
    year: int
    period_label: str          # "01 Jul 2026 - 31 Jul 2026"
    period_start: datetime
    period_end: datetime

    payment_date: Optional[datetime] = None
    payment_method: Optional[str] = None
    payment_reference: Optional[str] = None
    currency: str

    company: PayslipCompany
    employee: PayslipEmployee

    earnings: List[PayslipLine]
    deductions: List[PayslipLine]
    gross_earnings: float
    total_deductions: float
    # Basic pay less the approved unpaid-leave deduction — the figure income tax is actually
    # charged on. Stated on the document so the tax line can be checked rather
    # than taken on trust; allowances, bonus, arrears, overtime and
    # reimbursements are all exempt and so are excluded from it.
    taxable_income: float
    net_salary: float
    reconciliation: PayslipReconciliation

    # Populated only for a corrected payslip.
    supersedes: Optional[PayslipRevision] = None
    superseded_by: Optional[PayslipRevision] = None
    correction_reason: Optional[str] = None
    cancellation_reason: Optional[str] = None
    open_dispute_count: int = 0


class PayslipSummary(BaseModel):
    """One row in the period picker — enough to label it without the document."""
    id: int
    month: int
    year: int
    month_label: str
    status: PayrollStatusEnum
    net_salary: float
    payslip_no: Optional[str] = None
    payment_date: Optional[datetime] = None


class PayslipYTD(BaseModel):
    """Calendar-year totals, plus the previous month for comparison."""
    year: int
    currency: str
    gross_earnings: float
    total_deductions: float
    taxable_income: float
    tax_paid: float
    provident_fund: float
    overtime_total: float
    bonus_total: float
    net_paid: float
    months_counted: int
    previous_month_net: Optional[float] = None


class PayslipVerificationResponse(BaseModel):
    """Public-facing confirmation that a payslip is genuine.

    Intentionally minimal: it proves a document exists and matches, without
    exposing the salary breakdown to whoever holds the code.
    """
    valid: bool
    payslip_no: Optional[str] = None
    employee_name: Optional[str] = None
    period_label: Optional[str] = None
    status: Optional[PayrollStatusEnum] = None
    version: Optional[int] = None
    generated_at: Optional[datetime] = None
    net_salary: Optional[float] = None


# ----- Disputes -----

class DisputeCreate(BaseModel):
    category: DisputeCategoryEnum
    comment: str
    attachment_url: Optional[str] = None


class DisputeRespond(BaseModel):
    status: DisputeStatusEnum
    response: Optional[str] = None


class DisputeResponse(BaseModel):
    id: int
    payroll_id: int
    employee_id: int
    employee_name: Optional[str] = None
    payslip_no: Optional[str] = None
    period_label: Optional[str] = None
    category: DisputeCategoryEnum
    comment: str
    attachment_url: Optional[str] = None
    status: DisputeStatusEnum
    response: Optional[str] = None
    responder_name: Optional[str] = None
    responded_at: Optional[datetime] = None
    created_at: datetime

    class Config:
        from_attributes = True


class PayslipCorrectionRequest(BaseModel):
    reason: str


class PayslipCancellationRequest(BaseModel):
    reason: str
