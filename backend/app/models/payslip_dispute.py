from sqlalchemy import Column, Integer, String, Text, ForeignKey, DateTime, Enum
from sqlalchemy.orm import relationship
from datetime import datetime
import enum
from app.database import Base


class DisputeCategoryEnum(str, enum.Enum):
    INCORRECT_SALARY = "incorrect_salary"
    MISSING_ALLOWANCE = "missing_allowance"
    UNEXPECTED_DEDUCTION = "unexpected_deduction"
    TAX_ISSUE = "tax_issue"
    BANK_PAYMENT_ISSUE = "bank_payment_issue"
    OTHER = "other"


class DisputeStatusEnum(str, enum.Enum):
    OPEN = "open"
    IN_REVIEW = "in_review"
    RESOLVED = "resolved"
    REJECTED = "rejected"


class PayslipDispute(Base):
    """An employee's query about a specific payslip, and Payroll/HR's reply.

    Raised against a payslip rather than a period so the thread stays attached
    to the exact document version the employee was looking at — a correction
    issued later leaves this pointing at what they actually disputed.
    """
    __tablename__ = "payslip_disputes"

    id = Column(Integer, primary_key=True, index=True)
    payroll_id = Column(Integer, ForeignKey("payrolls.id"), nullable=False, index=True)
    employee_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)

    category = Column(Enum(DisputeCategoryEnum), nullable=False)
    comment = Column(Text, nullable=False)
    attachment_url = Column(String, nullable=True)

    status = Column(Enum(DisputeStatusEnum), default=DisputeStatusEnum.OPEN, nullable=False)
    response = Column(Text, nullable=True)
    responded_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    responded_at = Column(DateTime, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    payroll = relationship("Payroll", back_populates="disputes")
    employee = relationship("User", foreign_keys=[employee_id])
    responder = relationship("User", foreign_keys=[responded_by])

    def __repr__(self):
        return f"<PayslipDispute {self.category} on payroll={self.payroll_id} ({self.status})>"
