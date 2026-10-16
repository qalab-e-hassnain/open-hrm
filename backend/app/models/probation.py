"""
Probation, as a thing that ends.

Until now probation was an inference: three months added to the joining date,
consulted by the leave rules, never concluded. Nobody was formally confirmed,
nobody was told, and nothing recorded that a manager had ever formed a view.
"""
from sqlalchemy import (Column, Integer, String, Text, Date, DateTime, Enum,
                        ForeignKey)
from sqlalchemy.orm import relationship
from datetime import datetime
import enum

from app.database import Base


class ProbationStatusEnum(str, enum.Enum):
    IN_PROBATION = "in_probation"
    CONFIRMED = "confirmed"
    EXTENDED = "extended"
    # Set for anyone probation was never meant to apply to — a contractor, or
    # someone on payroll only.
    NOT_APPLICABLE = "not_applicable"


class ProbationDecisionEnum(str, enum.Enum):
    CONFIRM = "confirm"
    EXTEND = "extend"
    # A failed probation ends in a resignation or a termination handled
    # elsewhere; recorded here so the decision itself is not lost.
    NOT_CONFIRMED = "not_confirmed"


class ProbationReview(Base):
    __tablename__ = "probation_reviews"

    id = Column(Integer, primary_key=True, index=True)
    employee_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)

    # The end date this review was about, kept because an extension moves the
    # employee's date and the history would otherwise lose what was decided when.
    period_end = Column(Date, nullable=False)
    decision = Column(Enum(ProbationDecisionEnum), nullable=False)
    new_end_date = Column(Date, nullable=True)      # set when decision = extend

    comments = Column(Text, nullable=True)
    decided_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    decided_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # The confirmation letter issued off the back of this, when there is one.
    document_id = Column(Integer, ForeignKey("employee_documents.id"), nullable=True)

    employee = relationship("User", foreign_keys=[employee_id])
    reviewer = relationship("User", foreign_keys=[decided_by])
