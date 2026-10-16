from sqlalchemy import Column, Integer, Float, ForeignKey, DateTime, UniqueConstraint, String
from sqlalchemy.orm import relationship
from datetime import datetime
from app.database import Base


class LeaveBalance(Base):
    """
    Track employee leave balances based on the leave policy
    Updated: October 16, 2024
    """
    __tablename__ = "leave_balances"
    
    id = Column(Integer, primary_key=True, index=True)
    employee_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    
    # Annual Leave (24 days/year, 2 days/month accrual, 10 days carryover)
    # 14 since the revised policy effective 01 January 2026 (was 24).
    annual_total = Column(Float, default=14.0)
    annual_used = Column(Float, default=0.0)
    annual_carryover = Column(Float, default=0.0)  # Max 10 days from previous year
    
    # Sick Leave (15 days/year, no carryover)
    sick_total = Column(Float, default=15.0)
    sick_used = Column(Float, default=0.0)
    
    # Personal Leave (1 day/month = 12 days/year)
    personal_total = Column(Float, default=12.0)
    personal_used = Column(Float, default=0.0)
    
    # Maternity Leave (12 weeks = 60 working days)
    maternity_total = Column(Float, default=60.0)
    maternity_used = Column(Float, default=0.0)
    
    # Paternity Leave (2 weeks = 10 working days)
    paternity_total = Column(Float, default=10.0)
    paternity_used = Column(Float, default=0.0)
    
    # Bereavement Leave (5 days)
    bereavement_total = Column(Float, default=5.0)
    bereavement_used = Column(Float, default=0.0)
    
    # Year tracking
    year = Column(Integer, nullable=False)

    # One balance per employee PER YEAR. Uniqueness was on employee_id alone,
    # which made the year column a lie: opening 2027 would collide with the
    # employee's 2026 row and the first leave request of the year would fail.
    __table_args__ = (
        UniqueConstraint("employee_id", "year", name="uq_leave_balance_employee_year"),
    )
    
    # Metadata
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    employee = relationship("User", back_populates="leave_balance")
    
    def __repr__(self):
        return f"<LeaveBalance employee_id={self.employee_id} year={self.year}>"
    
    @property
    def annual_remaining(self):
        return self.annual_total + self.annual_carryover - self.annual_used
    
    @property
    def sick_remaining(self):
        return self.sick_total - self.sick_used
    
    @property
    def personal_remaining(self):
        return self.personal_total - self.personal_used
    
    @property
    def maternity_remaining(self):
        return self.maternity_total - self.maternity_used
    
    @property
    def paternity_remaining(self):
        return self.paternity_total - self.paternity_used
    
    @property
    def bereavement_remaining(self):
        return self.bereavement_total - self.bereavement_used

