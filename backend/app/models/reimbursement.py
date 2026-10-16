from sqlalchemy import Column, Integer, String, Float, ForeignKey, DateTime, Enum, Text
from sqlalchemy.orm import relationship
from datetime import datetime
import enum
from app.database import Base


class ReimbursementTypeEnum(str, enum.Enum):
    TRAVEL = "travel"
    FOOD = "food"
    MEDICAL = "medical"
    EQUIPMENT = "equipment"
    OTHER = "other"


class ReimbursementStatusEnum(str, enum.Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    PAID = "paid"


class Reimbursement(Base):
    __tablename__ = "reimbursements"
    
    id = Column(Integer, primary_key=True, index=True)
    employee_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    reimbursement_type = Column(Enum(ReimbursementTypeEnum), nullable=False)
    amount = Column(Float, nullable=False)
    description = Column(Text, nullable=False)
    receipt_url = Column(String)  # URL to uploaded receipt
    date_of_expense = Column(DateTime, nullable=False)
    status = Column(Enum(ReimbursementStatusEnum), default=ReimbursementStatusEnum.PENDING)
    
    # Approval details
    approved_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    approved_at = Column(DateTime, nullable=True)
    rejection_reason = Column(Text, nullable=True)
    paid_at = Column(DateTime, nullable=True)

    # Which payroll (if any) absorbed this approved reimbursement as an earning
    # line item. Set when the payroll for the period is generated/recomputed;
    # prevents the same reimbursement being counted on two payslips.
    payroll_id = Column(Integer, ForeignKey("payrolls.id"), nullable=True)
    
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    employee = relationship("User", back_populates="reimbursements", foreign_keys=[employee_id])
    approver = relationship("User", foreign_keys=[approved_by])
    
    def __repr__(self):
        return f"<Reimbursement ${self.amount} by employee_id={self.employee_id}>"

