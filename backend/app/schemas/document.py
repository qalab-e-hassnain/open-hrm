from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field

from app.schemas.datetimes import NaiveUTC

from app.models.document import DocumentKindEnum, DocumentStatusEnum


class JobProfileBase(BaseModel):
    designation: str = Field(..., min_length=2, max_length=120)
    department: Optional[str] = None
    reports_to_title: Optional[str] = None
    summary: Optional[str] = None
    responsibilities: List[str] = Field(default_factory=list)
    kpis: List[str] = Field(default_factory=list)


class JobProfileCreate(JobProfileBase):
    pass


class JobProfileUpdate(BaseModel):
    department: Optional[str] = None
    reports_to_title: Optional[str] = None
    summary: Optional[str] = None
    responsibilities: Optional[List[str]] = None
    kpis: Optional[List[str]] = None
    is_active: Optional[bool] = None


class JobProfileResponse(JobProfileBase):
    id: int
    is_active: bool
    employee_count: int = 0          # how many people hold this designation
    class Config:
        from_attributes = True


class TemplateCreate(BaseModel):
    kind: DocumentKindEnum
    title: str = Field(..., min_length=2, max_length=200)
    body_html: str = Field(..., min_length=20)
    requires_signature: bool = True
    include_in_onboarding: bool = False


class TemplateUpdate(BaseModel):
    """
    Editing publishes a new version rather than changing the current one, so
    documents already issued keep the text they were issued with.
    """
    title: Optional[str] = None
    body_html: Optional[str] = None
    requires_signature: Optional[bool] = None
    include_in_onboarding: Optional[bool] = None
    is_active: Optional[bool] = None


class TemplateResponse(BaseModel):
    id: int
    kind: DocumentKindEnum
    title: str
    version: int
    body_html: str
    requires_signature: bool
    include_in_onboarding: bool
    is_active: bool
    created_at: datetime
    class Config:
        from_attributes = True


class TemplatePreview(BaseModel):
    """A template rendered against a real employee, without issuing anything."""
    body_html: str
    missing_placeholders: List[str] = Field(default_factory=list)


class IssueDocumentRequest(BaseModel):
    employee_id: int
    template_id: int
    due_at: Optional[NaiveUTC] = None
    # Who signs for the Company. Left out, the issuer signs — which suits a
    # letter, but an employment agreement is signed by whoever is authorised
    # to bind the Company, not by the administrator who issued it.
    signatory_name: Optional[str] = Field(None, min_length=2, max_length=150)
    signatory_designation: Optional[str] = Field(None, max_length=150)


class SignRequest(BaseModel):
    # Typed rather than drawn: a typed name with an audit trail is the ordinary
    # simple-electronic-signature pattern, and needs no canvas.
    typed_name: str = Field(..., min_length=2, max_length=150)
    agreed: bool
    intent_statement: Optional[str] = None


class DeclineRequest(BaseModel):
    reason: str = Field(..., min_length=3, max_length=1000)


class SignatureInfo(BaseModel):
    typed_name: str
    signed_at: datetime
    ip_address: Optional[str] = None
    hash_at_signing: str
    intent_statement: Optional[str] = None
    class Config:
        from_attributes = True


class DocumentSummary(BaseModel):
    id: int
    employee_id: int
    employee_name: Optional[str] = None
    kind: DocumentKindEnum
    title: str
    status: DocumentStatusEnum
    requires_signature: bool
    issued_at: datetime
    due_at: Optional[NaiveUTC] = None
    viewed_at: Optional[datetime] = None
    class Config:
        from_attributes = True


class DocumentDetail(DocumentSummary):
    body_html: str
    content_hash: str
    verification_code: Optional[str] = None
    signature: Optional[SignatureInfo] = None
    # True when the stored text still hashes to what was signed. False means
    # the record has been tampered with and the document cannot be relied on.
    integrity_ok: bool = True


class DocumentVerification(BaseModel):
    """Public answer to "is this document genuine?" — no content disclosed."""
    valid: bool
    title: Optional[str] = None
    kind: Optional[DocumentKindEnum] = None
    employee_name: Optional[str] = None
    signed_at: Optional[datetime] = None
    content_hash: Optional[str] = None
    verified_at: Optional[datetime] = None
