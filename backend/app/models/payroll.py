from sqlalchemy import (Column, Integer, Float, ForeignKey, DateTime, String,
                        Enum, Boolean, Index, text)
from sqlalchemy.orm import relationship
from datetime import datetime
import enum
from app.database import Base


class PayrollStatusEnum(str, enum.Enum):
    """Payslip lifecycle.

    DRAFT/PROCESSED/PAID are the original three and keep their stored values —
    PROCESSED is what the UI labels "Finalized". PROCESSING, CORRECTED and
    CANCELLED were added later, so any query that groups statuses must handle
    all six.
    """
    DRAFT = "draft"
    PROCESSING = "processing"
    PROCESSED = "processed"      # finalized
    PAID = "paid"
    CORRECTED = "corrected"      # superseded by a newer version
    CANCELLED = "cancelled"


# Statuses an employee may treat as a real, final payslip.
FINAL_STATUSES = (PayrollStatusEnum.PROCESSED, PayrollStatusEnum.PAID)


class Payroll(Base):
    __tablename__ = "payrolls"
    
    id = Column(Integer, primary_key=True, index=True)
    employee_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    
    # Salary Components
    base_salary = Column(Float, nullable=False)
    allowances = Column(Float, default=0.0)
    bonuses = Column(Float, default=0.0)

    # --- Allowance itemisation -------------------------------------------
    # `allowances` above stays the authoritative total (existing payroll
    # screens and the tax logic read it). These break that total into the
    # lines a payslip must show and are kept summing to it by
    # _sync_payroll_components, so the printed payslip always reconciles.
    house_allowance = Column(Float, default=0.0, nullable=False)
    transport_allowance = Column(Float, default=0.0, nullable=False)
    medical_allowance = Column(Float, default=0.0, nullable=False)
    other_allowance = Column(Float, default=0.0, nullable=False)
    other_allowance_note = Column(String, nullable=True)

    # --- Deduction itemisation -------------------------------------------
    # Same contract: these sum to `deductions`. Statutory deductions are
    # separated from recoveries so each line can carry a real description
    # instead of a vague "other deduction".
    provident_fund = Column(Float, default=0.0, nullable=False)
    social_security = Column(Float, default=0.0, nullable=False)
    eobi = Column(Float, default=0.0, nullable=False)
    loan_repayment = Column(Float, default=0.0, nullable=False)
    loan_repayment_note = Column(String, nullable=True)      # "Installment 2 of 4"
    advance_recovery = Column(Float, default=0.0, nullable=False)
    advance_recovery_note = Column(String, nullable=True)
    other_deduction = Column(Float, default=0.0, nullable=False)
    other_deduction_note = Column(String, nullable=True)     # "Laptop damage recovery"
    arrears = Column(Float, default=0.0)  # back-pay; tax-exempt like allowances/bonuses
    # Earning line items swept in from approved requests (added AFTER tax —
    # neither is part of taxable income).
    reimbursement_total = Column(Float, default=0.0)  # sum of approved reimbursements for the period
    overtime_pay = Column(Float, default=0.0)          # flat overtime pay for approved overtime
    deductions = Column(Float, default=0.0)
    # Approved unpaid leave is auto-computed from the employee's own working
    # day: basic / (standard_hours x 20) per hour, x standard_hours per working
    # day. A part-timer is therefore docked their day, not a full one. It
    # reduces taxable basic and net pay.
    unpaid_leave_deduction = Column(Float, default=0.0)
    # Separate manual penalty entered by Admin/HR for unapproved absence.
    # Policy rate is Rs 5,000/day; keeping the amount explicit makes the
    # deduction auditable without pretending the system inferred the days.
    unapproved_leave_fine = Column(Float, default=0.0, nullable=False)
    tax = Column(Float, default=0.0)                    # auto-computed from PK salary slabs
    # True when the figure in `tax` was set by a person rather than derived
    # from the slab table, and must survive a recompute. Payroll-run figures
    # are the main source: an employee contributing to a pension scheme has
    # income exempt from salary tax, which the slab engine has no way to know,
    # so the admin's number is lower than the computed one and is correct.
    tax_is_manual = Column(Boolean, default=False, nullable=False, server_default="false")
    net_salary = Column(Float, nullable=False)

    # One payslip per employee per period, enforced by the database rather than
    # by whichever code path happens to create it. Payslips are now written by a
    # run finalize, a re-finalize, the manual endpoint and the backfills; an
    # application-level check in one of them protects none of the others.
    # One *live* payslip per employee per month. Superseded and cancelled rows
    # are excluded, because a correction works by keeping the original and
    # adding a new version beside it — and a plain unique constraint on the
    # period made that impossible, so /payslips/{id}/correct could never
    # succeed on any database that had it.
    __table_args__ = (
        Index("uq_payroll_active_period", "employee_id", "month", "year",
              unique=True,
              postgresql_where=text("status NOT IN ('CORRECTED', 'CANCELLED')"),
              sqlite_where=text("status NOT IN ('CORRECTED', 'CANCELLED')")),
    )
    
    # Period
    month = Column(Integer, nullable=False)  # 1-12
    year = Column(Integer, nullable=False)
    payment_date = Column(DateTime, nullable=True)
    
    payment_method = Column(String, nullable=True)      # e.g. "Bank Transfer"
    payment_reference = Column(String, nullable=True)    # payroll reference shown on the payslip

    status = Column(Enum(PayrollStatusEnum), default=PayrollStatusEnum.DRAFT)
    notes = Column(String)

    # --- Payslip identity & revisions -------------------------------------
    # Assigned once the payslip is finalized, never on a draft: a draft has no
    # payslip number to quote and must not be verifiable.
    payslip_no = Column(String, unique=True, index=True, nullable=True)      # QGT-PS-2026-07-0001
    verification_code = Column(String, unique=True, index=True, nullable=True)
    version = Column(Integer, default=1, nullable=False)
    generated_at = Column(DateTime, nullable=True)
    # A correction creates a NEW row pointing back at the one it replaces; the
    # old row moves to CORRECTED. That preserves the full revision history
    # instead of overwriting a document the employee may already have kept.
    supersedes_id = Column(Integer, ForeignKey("payrolls.id"), nullable=True)
    correction_reason = Column(String, nullable=True)
    cancelled_at = Column(DateTime, nullable=True)
    cancellation_reason = Column(String, nullable=True)
    # Backfilled record for a period predating the system. When True, the
    # admin-entered `tax` is trusted verbatim (not recomputed from current
    # slabs) and current reimbursements/overtime are NOT swept in.
    is_historical = Column(Boolean, default=False, nullable=False)
    
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    employee = relationship("User", back_populates="payrolls")
    # Self-join to the payslip this one replaces. remote_side pins the "one"
    # end of the self-referential FK so SQLAlchemy knows which way it points.
    supersedes = relationship("Payroll", remote_side=[id], uselist=False)
    disputes = relationship(
        "PayslipDispute",
        back_populates="payroll",
        cascade="all, delete-orphan",
    )

    def __repr__(self):
        return f"<Payroll {self.month}/{self.year} for employee_id={self.employee_id}>"

