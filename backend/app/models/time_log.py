from sqlalchemy import (Column, Integer, Float, String, Text, DateTime, Boolean,
                        ForeignKey, Enum, Index, event)
from sqlalchemy.orm import relationship
from datetime import datetime
import enum

from app.database import Base


class TimeLogKindEnum(str, enum.Enum):
    ATTENDANCE = "attendance"   # forgot to check in/out — record the hours worked
    OVERTIME = "overtime"       # extra hours claimed for pay


class TimeLogStatusEnum(str, enum.Enum):
    PENDING = "pending"                    # waiting on the line manager
    MANAGER_APPROVED = "manager_approved"  # waiting on HR/admin
    APPROVED = "approved"                  # both signatures in, effect applied
    REJECTED = "rejected"
    CANCELLED = "cancelled"                # withdrawn by the employee


class TimeLogRequest(Base):
    """
    An employee's claim about time they worked, requiring two signatures.

    Covers the two cases that cannot be trusted to the clock: a session nobody
    checked in for, and overtime. Both are self-reported, so both are worth
    less than an automatic record and are gated behind the line manager who
    saw the work and the HR manager who pays for it.

    One model rather than two because the approval machinery is identical and
    duplicating a state machine is how the two halves drift apart. `kind`
    decides only what happens on final approval.

    Amounts are entered, not derived. The system can suggest
    base_salary / 160 x hours, but what a person types is what gets paid —
    a rate table cannot know about a negotiated rate or a partial claim.
    """
    __tablename__ = "time_log_requests"

    id = Column(Integer, primary_key=True, index=True)
    employee_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    kind = Column(Enum(TimeLogKindEnum), nullable=False, index=True)

    # The day being claimed for. Times are stored UTC like every other
    # attendance row, so the client converts.
    work_date = Column(DateTime, nullable=False, index=True)
    check_in_time = Column(DateTime, nullable=True)    # attendance kind
    check_out_time = Column(DateTime, nullable=True)   # attendance kind
    hours = Column(Float, nullable=False, default=0.0)

    # Overtime only. `suggested_amount` is what the formula produced when the
    # request was filed, kept beside the claim so an approver can see whether
    # the entered figure departs from it and by how much.
    amount = Column(Float, nullable=True)
    suggested_amount = Column(Float, nullable=True)

    reason = Column(Text, nullable=False)

    status = Column(Enum(TimeLogStatusEnum), default=TimeLogStatusEnum.PENDING,
                    nullable=False, index=True)

    manager_approved_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    manager_approved_at = Column(DateTime, nullable=True)
    approved_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    approved_at = Column(DateTime, nullable=True)

    rejected_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    rejection_reason = Column(Text, nullable=True)

    # Set once an approved attendance claim has been written to `attendances`,
    # so a re-approval cannot create the same session twice.
    applied_attendance_id = Column(Integer, ForeignKey("attendances.id"), nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    employee = relationship("User", foreign_keys=[employee_id])
    manager_approver = relationship("User", foreign_keys=[manager_approved_by])
    approver = relationship("User", foreign_keys=[approved_by])


# Approver queues filter on status and scan by date; employees read their own.
Index("ix_time_log_status_date", TimeLogRequest.status, TimeLogRequest.work_date)


class SelfApproval(ValueError):
    """Raised when a claim would be signed off by the person who made it."""


def _refuse_self_approval(_mapper, _connection, target: "TimeLogRequest") -> None:
    """
    Nobody signs their own claim — not through the API, not from a script.

    The endpoint already refuses it, and that was not enough. A maintenance
    script wrote ten of these directly, naming the employee as both the line
    manager and the HR approver, and nothing objected: the rows looked exactly
    like properly approved claims to every report that read them. An auditor
    looking at those rows would see two signatures where there was one person.
    A rule that only exists in one endpoint is a rule that holds until
    somebody writes a second way in, so it lives on the model instead.

    An administrative correction is still perfectly possible — it just has to
    name the person who authorised it, which is the whole point.
    """
    for column in ("approved_by", "manager_approved_by"):
        approver = getattr(target, column, None)
        if approver is not None and approver == target.employee_id:
            raise SelfApproval(
                f"{column} is the same person as the employee "
                f"(id {target.employee_id}). A claim needs a signature from "
                f"somebody else; name the person who authorised it."
            )


event.listen(TimeLogRequest, "before_insert", _refuse_self_approval, propagate=True)
event.listen(TimeLogRequest, "before_update", _refuse_self_approval, propagate=True)
