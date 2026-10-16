"""
Documents an employee signs: contracts, NDAs, offer letters, policies.

Three tables, because the three things have different lifetimes. A template is
edited over time. An issued document is frozen the moment it is handed to
someone. A signature is evidence and is never touched again.
"""
from sqlalchemy import (Column, Integer, String, Text, DateTime, Boolean, Enum,
                        ForeignKey, JSON, UniqueConstraint, Index)
from sqlalchemy.orm import relationship
from datetime import datetime
import enum

from app.database import Base


class DocumentKindEnum(str, enum.Enum):
    CONTRACT = "contract"            # Key Employee Agreement
    NDA = "nda"                      # standalone, when not an appendix
    OFFER_LETTER = "offer_letter"
    HANDBOOK = "handbook"
    POLICY = "policy"                # leave, harassment, code of conduct
    NOC = "noc"                      # issued on exit
    CONFIRMATION = "confirmation"    # probation passed, employment confirmed
    OTHER = "other"


class DocumentStatusEnum(str, enum.Enum):
    ISSUED = "issued"                # sent, not yet opened
    VIEWED = "viewed"                # opened, not yet signed
    SIGNED = "signed"
    DECLINED = "declined"
    VOIDED = "voided"                # withdrawn by HR before signing


class JobProfile(Base):
    """
    A role's job description and KPIs, held as data rather than prose inside a
    contract file.

    This is what makes one contract template serve everyone: the clauses are
    identical, only Appendix A and B differ, and those come from here. It also
    means a JD can be reused for an appraisal or a job advert without being
    retyped out of a Word document.
    """
    __tablename__ = "job_profiles"

    id = Column(Integer, primary_key=True, index=True)
    designation = Column(String, nullable=False, unique=True, index=True)
    department = Column(String, nullable=True)
    # Whose title appears as "The Employee shall directly report to ___".
    reports_to_title = Column(String, nullable=True)

    summary = Column(Text, nullable=True)
    responsibilities = Column(JSON, nullable=False, default=list)
    kpis = Column(JSON, nullable=False, default=list)

    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class DocumentTemplate(Base):
    """
    The reusable body of a document, with {{placeholders}}.

    Versioned rather than edited in place: an issued document records which
    version it came from, so a clause changed in 2027 does not retroactively
    alter what somebody agreed to in 2025.
    """
    __tablename__ = "document_templates"

    id = Column(Integer, primary_key=True, index=True)
    kind = Column(Enum(DocumentKindEnum), nullable=False, index=True)
    title = Column(String, nullable=False)
    version = Column(Integer, nullable=False, default=1)

    body_html = Column(Text, nullable=False)

    # A policy is acknowledged; a contract is signed. Same table, and the flag
    # is what the UI keys its wording off.
    requires_signature = Column(Boolean, default=True, nullable=False)

    # Onboarding issues every template flagged here, so adding a new policy to
    # the pack is a data change rather than a deployment.
    include_in_onboarding = Column(Boolean, default=False, nullable=False)

    is_active = Column(Boolean, default=True, nullable=False)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("kind", "title", "version", name="uq_template_kind_title_version"),
    )


class EmployeeDocument(Base):
    """
    One issued copy, for one person.

    `body_html` is rendered at issue and never regenerated, and `content_hash`
    is taken over it. Together they answer the only question that matters
    later: is this exactly what they agreed to? Re-rendering on read would
    quietly rewrite history every time a template or a salary changed — the
    same reason PayrollEntry snapshots a salary.
    """
    __tablename__ = "employee_documents"

    id = Column(Integer, primary_key=True, index=True)
    employee_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    template_id = Column(Integer, ForeignKey("document_templates.id"), nullable=True)
    template_version = Column(Integer, nullable=True)

    kind = Column(Enum(DocumentKindEnum), nullable=False, index=True)
    title = Column(String, nullable=False)
    body_html = Column(Text, nullable=False)
    content_hash = Column(String, nullable=False, index=True)

    requires_signature = Column(Boolean, default=True, nullable=False)
    status = Column(Enum(DocumentStatusEnum), default=DocumentStatusEnum.ISSUED,
                    nullable=False, index=True)

    issued_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    issued_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    due_at = Column(DateTime, nullable=True)
    viewed_at = Column(DateTime, nullable=True)
    declined_at = Column(DateTime, nullable=True)
    decline_reason = Column(Text, nullable=True)

    # Lets a third party confirm a document is genuine, the same way payslips
    # and employment letters already can.
    verification_code = Column(String, unique=True, index=True, nullable=True)

    employee = relationship("User", foreign_keys=[employee_id])
    signature = relationship("DocumentSignature", back_populates="document", uselist=False)


class DocumentSignature(Base):
    """
    Evidence that a specific person agreed to a specific text at a specific
    moment. Append-only: never updated, never deleted.

    `hash_at_signing` is stored separately from the document's own hash so a
    later mismatch is detectable rather than invisible.
    """
    __tablename__ = "document_signatures"

    id = Column(Integer, primary_key=True, index=True)
    employee_document_id = Column(Integer, ForeignKey("employee_documents.id"),
                                  nullable=False, unique=True, index=True)

    typed_name = Column(String, nullable=False)
    signed_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    ip_address = Column(String, nullable=True)
    user_agent = Column(String, nullable=True)
    hash_at_signing = Column(String, nullable=False)

    # The employee ticks an explicit statement of intent; storing which words
    # they were shown matters more than that a box was ticked.
    intent_statement = Column(Text, nullable=True)

    document = relationship("EmployeeDocument", back_populates="signature")


Index("ix_employee_documents_employee_status", EmployeeDocument.employee_id,
      EmployeeDocument.status)
