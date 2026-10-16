"""
Salary advances and staff loans, with a schedule behind them.

Before this, an advance was a number typed into one payroll row with a
free-text note — the payroll model even suggests "Installment 2 of 4" as the
example. There was no principal, no schedule and no balance, so the only way
to answer "what does this person still owe" was to read a year of payslips.
"""
from sqlalchemy import (Column, Integer, String, Text, Date, DateTime, Float,
                        Enum, ForeignKey, Index)
from sqlalchemy.orm import relationship
from datetime import datetime
import enum

from app.database import Base


class LoanKindEnum(str, enum.Enum):
    # An advance is this month's salary paid early and recovered next month;
    # a loan is money lent and recovered over several. They deduct on different
    # payslip lines, which is why the distinction is stored rather than implied.
    ADVANCE = "advance"
    LOAN = "loan"


class LoanStatusEnum(str, enum.Enum):
    REQUESTED = "requested"
    APPROVED = "approved"        # agreed, not yet paid out
    DISBURSED = "disbursed"      # money paid, schedule running
    SETTLED = "settled"          # every instalment recovered or waived
    REJECTED = "rejected"
    CANCELLED = "cancelled"


class InstalmentStatusEnum(str, enum.Enum):
    PENDING = "pending"
    RECOVERED = "recovered"      # deducted on a payslip
    WAIVED = "waived"            # written off by an administrator


class EmployeeLoan(Base):
    __tablename__ = "employee_loans"

    id = Column(Integer, primary_key=True, index=True)
    employee_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    kind = Column(Enum(LoanKindEnum), nullable=False, default=LoanKindEnum.ADVANCE)

    principal = Column(Float, nullable=False)
    reason = Column(Text, nullable=False)

    status = Column(Enum(LoanStatusEnum), default=LoanStatusEnum.REQUESTED,
                    nullable=False, index=True)

    requested_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    requested_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    approved_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    approved_at = Column(DateTime, nullable=True)
    decision_note = Column(Text, nullable=True)

    disbursed_on = Column(Date, nullable=True)

    instalment_amount = Column(Float, nullable=True)
    instalment_count = Column(Integer, nullable=True)
    # "YYYY-MM" of the first payslip to carry a deduction. A string because
    # that is what it is compared against — the payroll month and year.
    first_recovery_month = Column(String, nullable=True)

    settled_at = Column(DateTime, nullable=True)
    notes = Column(Text, nullable=True)

    employee = relationship("User", foreign_keys=[employee_id])
    instalments = relationship("LoanInstalment", back_populates="loan",
                               cascade="all, delete-orphan",
                               order_by="LoanInstalment.sequence")

    @property
    def outstanding(self) -> float:
        """What is still to be recovered. Zero for anything not yet disbursed."""
        if self.status in (LoanStatusEnum.REQUESTED, LoanStatusEnum.APPROVED,
                           LoanStatusEnum.REJECTED, LoanStatusEnum.CANCELLED):
            return 0.0
        return round(sum(i.amount for i in self.instalments
                         if i.status == InstalmentStatusEnum.PENDING), 2)

    @property
    def recovered(self) -> float:
        return round(sum(i.amount for i in self.instalments
                         if i.status == InstalmentStatusEnum.RECOVERED), 2)


class LoanInstalment(Base):
    __tablename__ = "loan_instalments"

    id = Column(Integer, primary_key=True, index=True)
    loan_id = Column(Integer, ForeignKey("employee_loans.id"), nullable=False, index=True)
    sequence = Column(Integer, nullable=False)          # 1-based, for "2 of 4"
    due_month = Column(String, nullable=False, index=True)   # "2026-10"
    amount = Column(Float, nullable=False)

    status = Column(Enum(InstalmentStatusEnum), default=InstalmentStatusEnum.PENDING,
                    nullable=False, index=True)
    # The payslip that carried it. Set by the payroll sweep, cleared if that
    # payslip is ever removed, so nothing is recovered twice.
    payroll_id = Column(Integer, ForeignKey("payrolls.id"), nullable=True, index=True)
    recovered_at = Column(DateTime, nullable=True)
    waived_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    waived_reason = Column(Text, nullable=True)

    loan = relationship("EmployeeLoan", back_populates="instalments")


# The question the payroll sweep asks every month: what is due now, unpaid.
Index("ix_loan_instalments_due", LoanInstalment.due_month, LoanInstalment.status)
