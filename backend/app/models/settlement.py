"""
The final payment to someone who is leaving.

Computed from the record — service length, leave balance, outstanding advances,
equipment not returned — but never paid on the computation alone. Every
settlement starts as a draft that HR reviews and approves, because the policy
behind several of these lines is a company decision rather than a formula, and
because the last thing anyone should discover after the fact is that a machine
paid the wrong final salary.
"""
from sqlalchemy import (Column, Integer, String, Text, Date, DateTime, Float,
                        Enum, ForeignKey, JSON)
from sqlalchemy.orm import relationship
from datetime import datetime
import enum

from app.database import Base


class SettlementStatusEnum(str, enum.Enum):
    DRAFT = "draft"          # computed, editable, not binding
    APPROVED = "approved"    # signed off; the exit may now be finalised
    PAID = "paid"


class FinalSettlement(Base):
    __tablename__ = "final_settlements"

    id = Column(Integer, primary_key=True, index=True)
    resignation_id = Column(Integer, ForeignKey("resignations.id"),
                            nullable=False, unique=True, index=True)
    employee_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)

    status = Column(Enum(SettlementStatusEnum), default=SettlementStatusEnum.DRAFT,
                    nullable=False, index=True)
    last_working_day = Column(Date, nullable=False)
    computed_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # The salary the whole calculation rests on, snapshotted. A settlement
    # approved in October must not change because someone edits the employee's
    # salary in November.
    monthly_gross = Column(Float, default=0.0, nullable=False)

    # --- Earnings ---------------------------------------------------------
    final_month_days = Column(Float, default=0.0, nullable=False)      # working days served
    final_month_total_days = Column(Float, default=0.0, nullable=False)  # in that month
    final_month_amount = Column(Float, default=0.0, nullable=False)

    leave_encashment_days = Column(Float, default=0.0, nullable=False)
    leave_encashment_amount = Column(Float, default=0.0, nullable=False)

    gratuity_years = Column(Float, default=0.0, nullable=False)
    gratuity_amount = Column(Float, default=0.0, nullable=False)

    pending_reimbursements = Column(Float, default=0.0, nullable=False)
    other_earning = Column(Float, default=0.0, nullable=False)
    other_earning_note = Column(String, nullable=True)

    # --- Deductions -------------------------------------------------------
    notice_shortfall_days = Column(Float, default=0.0, nullable=False)
    notice_shortfall_amount = Column(Float, default=0.0, nullable=False)
    loan_outstanding = Column(Float, default=0.0, nullable=False)
    asset_recovery = Column(Float, default=0.0, nullable=False)
    # Left at zero for HR to fill: gratuity and encashment have their own tax
    # treatment, and guessing it here would be worse than leaving it visible
    # and empty.
    tax_deduction = Column(Float, default=0.0, nullable=False)
    other_deduction = Column(Float, default=0.0, nullable=False)
    other_deduction_note = Column(String, nullable=True)

    # --- Totals -----------------------------------------------------------
    gross_payable = Column(Float, default=0.0, nullable=False)
    total_deductions = Column(Float, default=0.0, nullable=False)
    net_payable = Column(Float, default=0.0, nullable=False)

    # Line names HR has overridden by hand. A recompute leaves these alone —
    # otherwise refreshing the draft after a late expense claim would silently
    # undo a negotiated figure.
    manual_fields = Column(JSON, default=list, nullable=False)

    notes = Column(Text, nullable=True)
    approved_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    approved_at = Column(DateTime, nullable=True)
    paid_at = Column(DateTime, nullable=True)
    payment_reference = Column(String, nullable=True)

    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    resignation = relationship("Resignation", foreign_keys=[resignation_id])
    employee = relationship("User", foreign_keys=[employee_id])
