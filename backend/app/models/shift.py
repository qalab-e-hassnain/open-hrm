"""
Working patterns: which hours, not just how many.

The portal already knows a part-time employee owes four hours a day. It has
never known *which* four, which makes "late" and "left early" unanswerable for
them, and leaves the fingerprint reconciliation able to check only that
somebody was present at all.
"""
from sqlalchemy import (Column, Integer, String, Time, Date, DateTime, Boolean,
                        ForeignKey, Index)
from sqlalchemy.orm import relationship
from datetime import datetime

from app.database import Base


class Shift(Base):
    __tablename__ = "shifts"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False, unique=True)     # "General", "Morning half"
    start_time = Column(Time, nullable=False)
    end_time = Column(Time, nullable=False)
    break_minutes = Column(Integer, default=0, nullable=False)

    # Monday-first, one character per day: "1111100" is Monday to Friday. A
    # string rather than seven columns because it is always read whole, and it
    # is legible in a database client, which seven booleans are not.
    days_of_week = Column(String, default="1111100", nullable=False)

    # Minutes past the start after which an arrival counts as late, and before
    # the end at which a departure counts as early. Policy, not arithmetic.
    grace_minutes = Column(Integer, default=15, nullable=False)

    is_active = Column(Boolean, default=True, nullable=False)
    is_default = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    assignments = relationship("ShiftAssignment", back_populates="shift")

    @property
    def span_hours(self) -> float:
        """
        How long somebody is at work: end minus start, break included.

        This is what an attendance session is measured against. The break is
        unpaid but nobody leaves the building for it, so capping a session at
        the paid figure logs everyone off early — which is exactly what
        happened when this shift was first assigned.
        """
        start = self.start_time.hour * 60 + self.start_time.minute
        end = self.end_time.hour * 60 + self.end_time.minute
        if end <= start:            # a shift crossing midnight
            end += 24 * 60
        return round((end - start) / 60.0, 2)

    @property
    def hours(self) -> float:
        """Paid hours in one shift, break excluded. Not the attendance cap."""
        start = self.start_time.hour * 60 + self.start_time.minute
        end = self.end_time.hour * 60 + self.end_time.minute
        if end <= start:            # a shift crossing midnight
            end += 24 * 60
        return round((end - start - (self.break_minutes or 0)) / 60.0, 2)

    def works_on(self, day) -> bool:
        pattern = self.days_of_week or "1111100"
        index = day.weekday()
        return index < len(pattern) and pattern[index] == "1"


class ShiftAssignment(Base):
    """
    Who works which pattern, and from when.

    Dated rather than a column on the employee: someone moving from full-time
    to part-time has a history, and last month's attendance has to keep being
    judged against last month's shift.
    """
    __tablename__ = "shift_assignments"

    id = Column(Integer, primary_key=True, index=True)
    employee_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    shift_id = Column(Integer, ForeignKey("shifts.id"), nullable=False, index=True)

    effective_from = Column(Date, nullable=False)
    effective_to = Column(Date, nullable=True)      # null = still in force

    assigned_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    shift = relationship("Shift", back_populates="assignments")
    employee = relationship("User", foreign_keys=[employee_id])


Index("ix_shift_assignments_employee_window", ShiftAssignment.employee_id,
      ShiftAssignment.effective_from, ShiftAssignment.effective_to)
