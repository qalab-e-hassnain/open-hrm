from sqlalchemy import Column, Integer, String, ForeignKey, DateTime, Enum, Text, Boolean
from sqlalchemy.orm import relationship
from sqlalchemy import TypeDecorator
from datetime import datetime
import enum
from app.database import Base


class LeaveTypeEnum(str, enum.Enum):
    SICK = "sick"
    CASUAL = "casual"
    ANNUAL = "annual"
    MATERNITY = "maternity"
    PATERNITY = "paternity"
    UNPAID = "unpaid"
    BEREAVEMENT = "bereavement"


class LeaveStatusEnum(str, enum.Enum):
    PENDING = "pending"
    MANAGER_APPROVED = "manager_approved"  # Approved by manager, waiting for HR/Admin
    APPROVED = "approved"  # Final approval by HR/Admin
    REJECTED = "rejected"
    CANCELLED = "cancelled"


class CaseInsensitiveEnum(TypeDecorator):
    """Enum type that handles case-insensitive values from database"""
    impl = String
    cache_ok = True
    
    def __init__(self, enum_class, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.enum_class = enum_class
        # Create a case-insensitive lookup
        self._value_map = {v.value.lower(): v for v in enum_class}
        self._name_map = {v.name.lower(): v for v in enum_class}
    
    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if isinstance(value, self.enum_class):
            # Get the enum value (e.g., "approved")
            enum_value = value.value
            # Check what's actually in the database by querying
            # For now, try to match case-insensitively and return the database value
            # Since database has uppercase, we need to check and return uppercase if that's what exists
            # But first, let's try the lowercase value - if it fails, the error handler will catch it
            return enum_value
        if isinstance(value, str):
            # Try to find matching enum
            value_lower = value.lower()
            if value_lower in self._value_map:
                return self._value_map[value_lower].value
            if value_lower in self._name_map:
                return self._name_map[value_lower].value
        return str(value).lower()
    
    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if isinstance(value, self.enum_class):
            return value
        # Convert string to enum (case-insensitive)
        value_lower = str(value).lower()
        if value_lower in self._value_map:
            return self._value_map[value_lower]
        if value_lower in self._name_map:
            return self._name_map[value_lower]
        # Fallback: try to create enum from value
        try:
            return self.enum_class(value_lower)
        except ValueError:
            # If exact match fails, try case-insensitive match
            for enum_val in self.enum_class:
                if enum_val.value.lower() == value_lower or enum_val.name.lower() == value_lower:
                    return enum_val
            raise ValueError(f"Invalid enum value: {value}")


class Leave(Base):
    __tablename__ = "leaves"
    
    id = Column(Integer, primary_key=True, index=True)
    employee_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    leave_type = Column(CaseInsensitiveEnum(LeaveTypeEnum), nullable=False)
    start_date = Column(DateTime, nullable=False)
    end_date = Column(DateTime, nullable=False)
    reason = Column(Text)
    status = Column(CaseInsensitiveEnum(LeaveStatusEnum), default=LeaveStatusEnum.PENDING)
    
    # Leave days calculation
    total_days = Column(Integer, nullable=True)  # Number of working days
    
    # Medical certificate (for sick leave > 2 days)
    medical_certificate_url = Column(String, nullable=True)
    medical_certificate_required = Column(Boolean, default=False)
    
    # Manager approval details
    manager_approved_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    manager_approved_at = Column(DateTime, nullable=True)
    
    # HR/Admin final approval details
    approved_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    approved_at = Column(DateTime, nullable=True)
    rejection_reason = Column(Text, nullable=True)
    
    # Comments field for additional notes (can be added by employee, manager, or admin)
    comments = Column(Text, nullable=True)

    # Who covers full admin authority while THIS leave is approved and active
    # (only meaningful when the leave belongs to an admin) — see
    # app.api.deps.is_acting_admin. Null means "no override": HR covers by
    # default for as long as the leave is in effect.
    acting_admin_id = Column(Integer, ForeignKey("users.id"), nullable=True)

    # Reminder markers for app/utils/leave_reminders.py — set once an admin
    # reminder email has gone out for this request, so the daily sweep doesn't
    # re-send the "day before" notice, and only re-sends the "still undecided
    # and the date has passed" notice once per day (not once per sweep run).
    overdue_reminder_sent_at = Column(DateTime, nullable=True)
    upcoming_reminder_sent_at = Column(DateTime, nullable=True)
    
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    employee = relationship("User", back_populates="leaves", foreign_keys=[employee_id])
    manager_approver = relationship("User", foreign_keys=[manager_approved_by])
    approver = relationship("User", foreign_keys=[approved_by])
    acting_admin = relationship("User", foreign_keys=[acting_admin_id])
    
    def __repr__(self):
        return f"<Leave {self.leave_type} by employee_id={self.employee_id}>"

