"""
Joining and leaving: an onboarding checklist, and a resignation that has to
clear three desks before it takes effect.
"""
from sqlalchemy import (Column, Integer, String, Text, Date, DateTime, Boolean,
                        Enum, ForeignKey, Index)
from sqlalchemy.orm import relationship
from datetime import datetime
import enum

from app.database import Base


class OnboardingTaskKindEnum(str, enum.Enum):
    DOCUMENT = "document"   # sign or acknowledge an issued document
    DATA = "data"           # supply something — bank details, CNIC scans
    ACTION = "action"       # somebody does something — issue a laptop


class TaskStatusEnum(str, enum.Enum):
    PENDING = "pending"
    COMPLETED = "completed"
    WAIVED = "waived"


class OnboardingPacket(Base):
    """One new joiner's checklist. Complete when every task is."""
    __tablename__ = "onboarding_packets"

    id = Column(Integer, primary_key=True, index=True)
    employee_id = Column(Integer, ForeignKey("users.id"), nullable=False,
                         unique=True, index=True)
    started_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    completed_at = Column(DateTime, nullable=True)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)

    tasks = relationship("OnboardingTask", back_populates="packet",
                         cascade="all, delete-orphan")
    employee = relationship("User", foreign_keys=[employee_id])


class OnboardingTask(Base):
    """
    One item on the checklist.

    Document tasks are not the only kind on purpose: "collect bank details" and
    "issue a laptop" belong on the same list as "sign the NDA", or a new joiner
    has to track their first week in two places.
    """
    __tablename__ = "onboarding_tasks"

    id = Column(Integer, primary_key=True, index=True)
    packet_id = Column(Integer, ForeignKey("onboarding_packets.id"),
                       nullable=False, index=True)
    kind = Column(Enum(OnboardingTaskKindEnum), nullable=False)
    label = Column(String, nullable=False)
    detail = Column(Text, nullable=True)
    sort_order = Column(Integer, default=0, nullable=False)

    # Set for kind=document; the task completes when the document is signed.
    employee_document_id = Column(Integer, ForeignKey("employee_documents.id"), nullable=True)

    status = Column(Enum(TaskStatusEnum), default=TaskStatusEnum.PENDING,
                    nullable=False, index=True)
    completed_at = Column(DateTime, nullable=True)
    completed_by = Column(Integer, ForeignKey("users.id"), nullable=True)

    packet = relationship("OnboardingPacket", back_populates="tasks")


class ResignationStatusEnum(str, enum.Enum):
    SUBMITTED = "submitted"                # awaiting the line manager
    MANAGER_APPROVED = "manager_approved"  # awaiting an admin (CEO chain)
    CEO_APPROVED = "ceo_approved"          # awaiting HR clearance
    CLEARED = "cleared"                    # clearance done, waiting for the last day
    COMPLETED = "completed"                # last day reached, account closed
    REJECTED = "rejected"
    WITHDRAWN = "withdrawn"                # employee changed their mind


class Resignation(Base):
    """
    A resignation moving through line manager, an admin, then HR clearance.

    The last working day is stored rather than derived: the notice period is a
    default, not a rule, and an admin may agree a different date. Completion is
    driven by that date, so the account stays live through the notice period
    instead of closing on approval.
    """
    __tablename__ = "resignations"

    id = Column(Integer, primary_key=True, index=True)
    employee_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)

    submitted_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    reason = Column(Text, nullable=False)
    notice_days = Column(Integer, default=30, nullable=False)
    proposed_last_working_day = Column(Date, nullable=False)
    agreed_last_working_day = Column(Date, nullable=True)

    status = Column(Enum(ResignationStatusEnum), default=ResignationStatusEnum.SUBMITTED,
                    nullable=False, index=True)

    manager_approved_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    manager_approved_at = Column(DateTime, nullable=True)
    manager_note = Column(Text, nullable=True)

    ceo_approved_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    ceo_approved_at = Column(DateTime, nullable=True)
    ceo_note = Column(Text, nullable=True)

    hr_cleared_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    hr_cleared_at = Column(DateTime, nullable=True)

    rejected_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    rejection_reason = Column(Text, nullable=True)
    withdrawn_at = Column(DateTime, nullable=True)

    completed_at = Column(DateTime, nullable=True)
    exit_pack_sent_at = Column(DateTime, nullable=True)
    noc_document_id = Column(Integer, ForeignKey("employee_documents.id"), nullable=True)

    employee = relationship("User", foreign_keys=[employee_id])
    clearance_items = relationship("ClearanceItem", back_populates="resignation",
                                   cascade="all, delete-orphan")


class ClearanceItem(Base):
    """
    One thing HR must settle before someone leaves.

    `reference` points at whatever the item is about — for an equipment line,
    the id of the asset in the register, so ticking the box and handing the
    laptop back are the same fact rather than two.
    """
    __tablename__ = "clearance_items"

    id = Column(Integer, primary_key=True, index=True)
    resignation_id = Column(Integer, ForeignKey("resignations.id"),
                            nullable=False, index=True)
    category = Column(String, nullable=False)      # assets | handover | finance | access
    label = Column(String, nullable=False)
    reference = Column(String, nullable=True)
    status = Column(Enum(TaskStatusEnum), default=TaskStatusEnum.PENDING, nullable=False)
    notes = Column(Text, nullable=True)
    cleared_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    cleared_at = Column(DateTime, nullable=True)

    resignation = relationship("Resignation", back_populates="clearance_items")


Index("ix_resignations_status_lastday", Resignation.status,
      Resignation.agreed_last_working_day)
