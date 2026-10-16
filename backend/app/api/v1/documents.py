"""
Contracts, NDAs and policies: issued to a person, signed in the portal.

Rendering happens once, at issue, and the result is hashed. Everything after
that reads the stored text — a document is evidence, and evidence that
re-renders itself is not evidence.
"""
import secrets
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session, joinedload

from app.api.deps import get_current_user, is_privileged, require_admin_or_hr
from app.database import get_db
from app.models.document import (JobProfile, DocumentTemplate, EmployeeDocument,
                                 DocumentSignature, DocumentKindEnum, DocumentStatusEnum)
from app.models.user import User
from app.schemas.document import (JobProfileCreate, JobProfileUpdate, JobProfileResponse,
                                  TemplateCreate, TemplateUpdate, TemplateResponse,
                                  TemplatePreview, IssueDocumentRequest, SignRequest,
                                  DeclineRequest, DocumentSummary, DocumentDetail,
                                  SignatureInfo, DocumentVerification)
from app.utils import audit
from app.utils.documents import build_context, render, content_hash, missing_placeholders
from app.utils.notifications import create_notification
from app.models.notification import NotificationTypeEnum

router = APIRouter()

DEFAULT_INTENT = ("I have read this document in full and I agree to be bound by it. "
                  "I understand that typing my name constitutes my signature.")


# ---------------------------------------------------------------------------
# Job profiles — the JD and KPIs a contract pulls in
# ---------------------------------------------------------------------------

@router.get("/job-profiles", response_model=List[JobProfileResponse])
def list_job_profiles(db: Session = Depends(get_db),
                      current_user: User = Depends(require_admin_or_hr)):
    profiles = db.query(JobProfile).order_by(JobProfile.designation).all()
    counts = {}
    for u in db.query(User).filter(User.is_active == True).all():  # noqa: E712
        counts[u.designation] = counts.get(u.designation, 0) + 1
    out = []
    for p in profiles:
        r = JobProfileResponse.model_validate(p)
        r.employee_count = counts.get(p.designation, 0)
        out.append(r)
    return out


@router.post("/job-profiles", response_model=JobProfileResponse,
             status_code=status.HTTP_201_CREATED)
def create_job_profile(payload: JobProfileCreate, db: Session = Depends(get_db),
                       current_user: User = Depends(require_admin_or_hr)):
    if db.query(JobProfile).filter(JobProfile.designation == payload.designation).first():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail=f"A job profile for '{payload.designation}' already exists.")
    profile = JobProfile(**payload.model_dump())
    db.add(profile); db.commit(); db.refresh(profile)
    return JobProfileResponse.model_validate(profile)


@router.put("/job-profiles/{profile_id}", response_model=JobProfileResponse)
def update_job_profile(profile_id: int, payload: JobProfileUpdate,
                       db: Session = Depends(get_db),
                       current_user: User = Depends(require_admin_or_hr)):
    profile = db.query(JobProfile).filter(JobProfile.id == profile_id).first()
    if not profile:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job profile not found")
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(profile, k, v)
    db.commit(); db.refresh(profile)
    return JobProfileResponse.model_validate(profile)


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Authoring aids: what a template may use, and what to start from
# ---------------------------------------------------------------------------

@router.get("/placeholders")
def list_placeholders(employee_id: Optional[int] = None, db: Session = Depends(get_db),
                      current_user: User = Depends(require_admin_or_hr)):
    """
    Every placeholder a template may use, grouped, with a worked example.

    Pass an employee_id and the examples are that person's real values, so an
    author picking {{gross_salary_words}} sees "Pakistan Rupees Three Lakh only"
    rather than a description of it. Served from the same context builder the
    renderer uses, so this list cannot drift from what actually substitutes.
    """
    from app.utils.doc_starters import PLACEHOLDER_GROUPS

    sample = None
    if employee_id:
        sample = db.query(User).filter(User.id == employee_id).first()
    if sample is None:
        sample = (db.query(User).filter(User.is_active == True)  # noqa: E712
                  .order_by(User.id).first())

    ctx = build_context(db, sample, extra={
        "signatory_name": current_user.full_name,
        "signatory_designation": current_user.designation or "",
    }) if sample else {}

    def example(key):
        v = ctx.get(key, "")
        # The bullet-list values are markup; show them as a readable summary.
        if key in ("job_responsibilities", "kpis"):
            return f"{v.count('<li>')} bullet point(s)"
        return (v[:80] + "…") if len(str(v)) > 80 else v

    return {
        "sample_employee": sample.full_name if sample else None,
        "groups": [
            {"name": name,
             "placeholders": [{"key": k, "description": d, "example": example(k)}
                              for k, d in items]}
            for name, items in PLACEHOLDER_GROUPS
        ],
    }


@router.get("/starters")
def list_starters(current_user: User = Depends(require_admin_or_hr)):
    """
    Skeletons to start a new template from.

    Not finished legal text, and deliberately so — a plausible-looking NDA that
    nobody wrote would eventually be issued. Each carries the document's shape
    and marks where the company's own wording belongs.
    """
    from app.utils.doc_starters import STARTERS

    return [{"key": key, **value} for key, value in STARTERS.items()]


@router.get("/templates", response_model=List[TemplateResponse])
def list_templates(include_inactive: bool = False, db: Session = Depends(get_db),
                   current_user: User = Depends(require_admin_or_hr)):
    q = db.query(DocumentTemplate)
    if not include_inactive:
        q = q.filter(DocumentTemplate.is_active == True)  # noqa: E712
    return q.order_by(DocumentTemplate.kind, DocumentTemplate.title,
                      DocumentTemplate.version.desc()).all()


@router.post("/templates", response_model=TemplateResponse,
             status_code=status.HTTP_201_CREATED)
def create_template(payload: TemplateCreate, db: Session = Depends(get_db),
                    current_user: User = Depends(require_admin_or_hr)):
    latest = db.query(DocumentTemplate).filter(
        DocumentTemplate.kind == payload.kind,
        DocumentTemplate.title == payload.title,
    ).order_by(DocumentTemplate.version.desc()).first()
    tpl = DocumentTemplate(**payload.model_dump(),
                           version=(latest.version + 1) if latest else 1,
                           created_by=current_user.id)
    db.add(tpl); db.commit(); db.refresh(tpl)
    return tpl


@router.put("/templates/{template_id}", response_model=TemplateResponse)
def update_template(template_id: int, payload: TemplateUpdate,
                    db: Session = Depends(get_db),
                    current_user: User = Depends(require_admin_or_hr)):
    """
    Changing the body publishes a new version and retires the old one, so a
    clause edited today cannot alter what somebody agreed to last year.
    Flags alone are edited in place.
    """
    tpl = db.query(DocumentTemplate).filter(DocumentTemplate.id == template_id).first()
    if not tpl:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Template not found")
    data = payload.model_dump(exclude_unset=True)

    if "body_html" in data and data["body_html"] != tpl.body_html:
        fresh = DocumentTemplate(
            kind=tpl.kind, title=data.get("title", tpl.title),
            version=tpl.version + 1, body_html=data["body_html"],
            requires_signature=data.get("requires_signature", tpl.requires_signature),
            include_in_onboarding=data.get("include_in_onboarding", tpl.include_in_onboarding),
            created_by=current_user.id,
        )
        tpl.is_active = False
        db.add(fresh); db.commit(); db.refresh(fresh)
        return fresh

    for k, v in data.items():
        setattr(tpl, k, v)
    db.commit(); db.refresh(tpl)
    return tpl


@router.get("/templates/{template_id}/preview/{employee_id}", response_model=TemplatePreview)
def preview_template(template_id: int, employee_id: int, db: Session = Depends(get_db),
                     current_user: User = Depends(require_admin_or_hr)):
    """
    See the document as it would be issued, without issuing it — and see which
    placeholders this employee cannot fill, before a contract goes out with a
    blank salary on it.
    """
    tpl = db.query(DocumentTemplate).filter(DocumentTemplate.id == template_id).first()
    employee = db.query(User).filter(User.id == employee_id).first()
    if not tpl or not employee:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="Template or employee not found")
    ctx = build_context(db, employee, extra={
        "signatory_name": current_user.full_name,
        "signatory_designation": current_user.designation or "",
    })
    return TemplatePreview(body_html=render(tpl.body_html, ctx),
                           missing_placeholders=missing_placeholders(tpl.body_html, ctx))


# ---------------------------------------------------------------------------
# Issuing and signing
# ---------------------------------------------------------------------------

def issue_document(db: Session, employee: User, template: DocumentTemplate,
                   issued_by: User, due_at=None,
                   signatory_name: Optional[str] = None,
                   signatory_designation: Optional[str] = None) -> EmployeeDocument:
    """
    Render once, hash it, and hand it over. Shared with onboarding.

    The signatory defaults to whoever issued the document, which is right for
    a letter HR sends out. It is not right for an employment agreement: the
    person who presses the button is an administrator, while the person who
    binds the Company is whoever is authorised to sign for it. Naming them
    separately keeps the signature block truthful without pretending the
    administrator signed.
    """
    ctx = build_context(db, employee, extra={
        "signatory_name": signatory_name or issued_by.full_name,
        "signatory_designation": (signatory_designation
                                  if signatory_designation is not None
                                  else (issued_by.designation or "")),
    })
    body = render(template.body_html, ctx)
    doc = EmployeeDocument(
        employee_id=employee.id, template_id=template.id,
        template_version=template.version, kind=template.kind,
        title=template.title, body_html=body, content_hash=content_hash(body),
        requires_signature=template.requires_signature,
        status=DocumentStatusEnum.ISSUED, issued_by=issued_by.id, due_at=due_at,
        verification_code=secrets.token_urlsafe(12),
    )
    db.add(doc)
    return doc


@router.post("/issue", response_model=DocumentDetail, status_code=status.HTTP_201_CREATED)
def issue(payload: IssueDocumentRequest, request: Request, db: Session = Depends(get_db),
          current_user: User = Depends(require_admin_or_hr)):
    employee = db.query(User).filter(User.id == payload.employee_id).first()
    tpl = db.query(DocumentTemplate).filter(DocumentTemplate.id == payload.template_id).first()
    if not employee or not tpl:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="Employee or template not found")
    doc = issue_document(db, employee, tpl, current_user, payload.due_at,
                         signatory_name=payload.signatory_name,
                         signatory_designation=payload.signatory_designation)
    db.commit(); db.refresh(doc)

    try:
        create_notification(db=db, user_id=employee.id, title=f"{tpl.title} to sign",
                            message=f"{tpl.title} has been issued to you and is waiting for your signature.",
                            notification_type=NotificationTypeEnum.INFO)
        db.commit()
    except Exception:
        db.rollback()

    audit.record(db, "document.issued", actor=current_user, request=request,
                 target_type="employee_document", target_id=doc.id,
                 detail={"employee_id": employee.id, "kind": tpl.kind.value,
                         "title": tpl.title, "version": tpl.version,
                         "signatory": payload.signatory_name or current_user.full_name})
    return _detail(db, doc, current_user)


def _detail(db: Session, doc: EmployeeDocument, viewer: User) -> DocumentDetail:
    d = DocumentDetail.model_validate(doc)
    d.employee_name = doc.employee.full_name if doc.employee else None
    sig = db.query(DocumentSignature).filter(
        DocumentSignature.employee_document_id == doc.id).first()
    d.signature = SignatureInfo.model_validate(sig) if sig else None
    # Recomputed on every read: a stored hash that no longer matches the stored
    # text is the one thing a signature record must never hide.
    d.integrity_ok = content_hash(doc.body_html) == doc.content_hash
    return d


def _may_read(db: Session, doc: EmployeeDocument, user: User) -> bool:
    return doc.employee_id == user.id or is_privileged(user, db)


@router.get("/me", response_model=List[DocumentSummary])
def my_documents(db: Session = Depends(get_db),
                 current_user: User = Depends(get_current_user)):
    """
    An employee's own documents.

    Voided ones are left out: a document withdrawn by HR is not something the
    employee has to act on or keep, and listing it invites them to open a
    superseded contract. The row is kept — HR still sees it on the employee's
    record, and the signature on it stays as evidence — it is just not the
    employee's business any more.
    """
    docs = (db.query(EmployeeDocument).options(joinedload(EmployeeDocument.employee))
            .filter(EmployeeDocument.employee_id == current_user.id,
                    EmployeeDocument.status != DocumentStatusEnum.VOIDED)
            .order_by(EmployeeDocument.issued_at.desc()).all())
    out = []
    for d in docs:
        s = DocumentSummary.model_validate(d)
        s.employee_name = current_user.full_name
        out.append(s)
    return out


@router.get("/employee/{employee_id}", response_model=List[DocumentSummary])
def employee_documents(employee_id: int, include_voided: bool = False,
                       db: Session = Depends(get_db),
                       current_user: User = Depends(get_current_user)):
    """
    One person's documents. Voided ones are hidden unless asked for, and only
    a privileged caller may ask — an employee reading their own record gets
    the same list as /me.
    """
    privileged = is_privileged(current_user, db)
    if employee_id != current_user.id and not privileged:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="Not enough permissions")
    q = (db.query(EmployeeDocument).options(joinedload(EmployeeDocument.employee))
         .filter(EmployeeDocument.employee_id == employee_id))
    if not (include_voided and privileged):
        q = q.filter(EmployeeDocument.status != DocumentStatusEnum.VOIDED)
    docs = q.order_by(EmployeeDocument.issued_at.desc()).all()
    out = []
    for d in docs:
        s = DocumentSummary.model_validate(d)
        s.employee_name = d.employee.full_name if d.employee else None
        out.append(s)
    return out


@router.get("/{doc_id}", response_model=DocumentDetail)
def read_document(doc_id: int, db: Session = Depends(get_db),
                  current_user: User = Depends(get_current_user)):
    """Opening an unsigned document marks it viewed — part of the evidence."""
    doc = (db.query(EmployeeDocument).options(joinedload(EmployeeDocument.employee))
           .filter(EmployeeDocument.id == doc_id).first())
    if not doc or not _may_read(db, doc, current_user):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    if doc.employee_id == current_user.id and doc.status == DocumentStatusEnum.ISSUED:
        doc.status = DocumentStatusEnum.VIEWED
        doc.viewed_at = datetime.utcnow()
        db.commit(); db.refresh(doc)
    return _detail(db, doc, current_user)


@router.post("/{doc_id}/sign", response_model=DocumentDetail)
def sign_document(doc_id: int, payload: SignRequest, request: Request,
                  db: Session = Depends(get_db),
                  current_user: User = Depends(get_current_user)):
    """
    Sign your own document.

    Only the employee named on it may sign, including admins — a signature
    applied by anyone else is not a signature. The hash is recomputed and
    stored with the signature so the text can be proved unchanged afterwards.
    """
    doc = (db.query(EmployeeDocument).options(joinedload(EmployeeDocument.employee))
           .filter(EmployeeDocument.id == doc_id).first())
    if not doc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    if doc.employee_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="Only the person a document was issued to can sign it.")
    if doc.status == DocumentStatusEnum.SIGNED:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="This document is already signed.")
    if doc.status in (DocumentStatusEnum.DECLINED, DocumentStatusEnum.VOIDED):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail=f"This document is {doc.status.value} and cannot be signed.")
    if not payload.agreed:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Agreement must be confirmed before signing.")

    current = content_hash(doc.body_html)
    if current != doc.content_hash:
        # The stored text no longer matches what was issued. Signing it would
        # attest to something nobody approved.
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="This document failed its integrity check and cannot be signed. "
                                   "Contact HR.")

    sig = DocumentSignature(
        employee_document_id=doc.id, typed_name=payload.typed_name.strip(),
        ip_address=audit.client_ip(request),
        user_agent=(request.headers.get("user-agent") or "")[:500],
        hash_at_signing=current,
        intent_statement=payload.intent_statement or DEFAULT_INTENT,
    )
    db.add(sig)
    doc.status = DocumentStatusEnum.SIGNED
    db.commit(); db.refresh(doc)

    # Signing a document may be the thing an onboarding task was waiting for.
    from app.api.v1.onboarding import complete_document_task
    complete_document_task(db, doc.id)

    audit.record(db, "document.signed", actor=current_user, request=request,
                 target_type="employee_document", target_id=doc.id,
                 detail={"title": doc.title, "hash": current})
    return _detail(db, doc, current_user)


@router.post("/{doc_id}/decline", response_model=DocumentDetail)
def decline_document(doc_id: int, payload: DeclineRequest, request: Request,
                     db: Session = Depends(get_db),
                     current_user: User = Depends(get_current_user)):
    """Refusing has to be possible, and has to be recorded."""
    doc = db.query(EmployeeDocument).filter(EmployeeDocument.id == doc_id).first()
    if not doc or doc.employee_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    if doc.status == DocumentStatusEnum.SIGNED:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="This document is already signed.")
    doc.status = DocumentStatusEnum.DECLINED
    doc.declined_at = datetime.utcnow()
    doc.decline_reason = payload.reason
    db.commit(); db.refresh(doc)
    audit.record(db, "document.declined", actor=current_user, request=request,
                 target_type="employee_document", target_id=doc.id,
                 detail={"title": doc.title, "reason": payload.reason})
    return _detail(db, doc, current_user)


@router.get("/verify/{code}", response_model=DocumentVerification)
def verify_document(code: str, db: Session = Depends(get_db)):
    """
    Confirm a document is genuine, without authentication and without
    disclosing its contents — same shape as payslip and employment-letter
    verification. Only signed documents verify; an unsigned draft is not a
    document anybody should be relying on.
    """
    doc = db.query(EmployeeDocument).filter(
        EmployeeDocument.verification_code == code).first() if code else None
    if not doc or doc.status != DocumentStatusEnum.SIGNED:
        return DocumentVerification(valid=False)
    if content_hash(doc.body_html) != doc.content_hash:
        return DocumentVerification(valid=False)
    sig = db.query(DocumentSignature).filter(
        DocumentSignature.employee_document_id == doc.id).first()
    return DocumentVerification(
        valid=True, title=doc.title, kind=doc.kind,
        employee_name=doc.employee.full_name if doc.employee else None,
        signed_at=sig.signed_at if sig else None,
        content_hash=doc.content_hash, verified_at=datetime.utcnow(),
    )
