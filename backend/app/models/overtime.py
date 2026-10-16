from sqlalchemy import (Column, Integer, Float, ForeignKey, DateTime, Enum,
                        Boolean, UniqueConstraint)
from sqlalchemy.orm import relationship
from datetime import datetime
import enum
from app.database import Base


class OvertimeStatusEnum(str, enum.Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class OvertimeRecord(Base):
    """
    One employee's total overtime for a pay period (month/year), acting as the
    admin approval gate. Hours are derived from attendance; the PAY is computed
    at payroll generation time using that payroll's base salary
    (base_salary / 160 * overtime_hours). Only APPROVED records feed the payslip.
    """
    __tablename__ = "overtime_records"

    id = Column(Integer, primary_key=True, index=True)
    employee_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    month = Column(Integer, nullable=False)  # 1-12
    year = Column(Integer, nullable=False)

    overtime_hours = Column(Float, default=0.0, nullable=False)  # from attendance
    overtime_pay = Column(Float, default=0.0)  # stamped once attached to a payroll
    # True when the amount came from approved manual claims rather than the
    # salary formula. A negotiated or partial claim is not something a rate
    # table can reproduce, so payroll must not recompute over it.
    pay_is_manual = Column(Boolean, default=False, nullable=False, server_default="false")

    status = Column(Enum(OvertimeStatusEnum), default=OvertimeStatusEnum.PENDING, nullable=False)
    approved_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    approved_at = Column(DateTime, nullable=True)

    # Which payroll absorbed this approved overtime (set at payroll recompute).
    payroll_id = Column(Integer, ForeignKey("payrolls.id"), nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # One overtime record per employee per period.
    __table_args__ = (
        UniqueConstraint("employee_id", "month", "year", name="uq_overtime_employee_period"),
    )

    employee = relationship("User", foreign_keys=[employee_id])
    approver = relationship("User", foreign_keys=[approved_by])

    def __repr__(self):
        return f"<OvertimeRecord {self.month}/{self.year} employee_id={self.employee_id} {self.status}>"
