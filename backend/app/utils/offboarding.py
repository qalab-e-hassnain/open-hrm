"""
Finalising an exit: issue the NOC, send the exit pack, close the account.
"""
import logging
import secrets
from datetime import datetime

from sqlalchemy.orm import Session

from app.config import settings
from app.models.document import (DocumentTemplate, EmployeeDocument, DocumentKindEnum,
                                 DocumentStatusEnum)
from app.models.lifecycle import Resignation, ResignationStatusEnum
from app.models.payroll import Payroll
from app.models.user import User
from app.utils.documents import build_context, render, content_hash

logger = logging.getLogger(__name__)

# Used when no NOC template has been configured, so an exit is never blocked by
# a missing template — HR can replace it with their own wording at any time.
FALLBACK_NOC = """
<h2>No Objection Certificate</h2>
<p>This is to certify that <strong>{{employee_name}}</strong>
(Employee ID {{employee_id}}, CNIC {{employee_cnic}}) was employed with
{{company_name}} as <strong>{{designation}}</strong> from {{date_of_joining}}
to {{last_working_day}}.</p>
<p>{{Pronoun_subject}} has completed all responsibilities and organisational
formalities, and the Company has no objection to {{pronoun_possessive}} future
employment elsewhere.</p>
<p>We wish {{pronoun_object}} success in {{pronoun_possessive}} future endeavours.</p>
<p>{{company_name}}<br>NTN {{company_ntn}}<br>{{company_address}}</p>
"""


def issue_noc(db: Session, resignation: Resignation, actor: User) -> EmployeeDocument:
    """
    Issue the leaving certificate.

    Rendered and hashed like any other document, and given a verification code
    so a future employer can confirm it through the public endpoint rather than
    phoning HR.
    """
    employee = resignation.employee or db.query(User).filter(
        User.id == resignation.employee_id).first()
    last_day = resignation.agreed_last_working_day or resignation.proposed_last_working_day

    template = db.query(DocumentTemplate).filter(
        DocumentTemplate.kind == DocumentKindEnum.NOC,
        DocumentTemplate.is_active == True,          # noqa: E712
    ).order_by(DocumentTemplate.version.desc()).first()

    ctx = build_context(db, employee, extra={
        "last_working_day": last_day.strftime("%d %B %Y") if last_day else "",
        "signatory_name": actor.full_name,
        "signatory_designation": actor.designation or "",
    })
    body = render(template.body_html if template else FALLBACK_NOC, ctx)

    doc = EmployeeDocument(
        employee_id=employee.id,
        template_id=template.id if template else None,
        template_version=template.version if template else None,
        kind=DocumentKindEnum.NOC,
        title=template.title if template else "No Objection Certificate",
        body_html=body, content_hash=content_hash(body),
        # Issued by the company, not signed by the employee.
        requires_signature=False, status=DocumentStatusEnum.SIGNED,
        issued_by=actor.id, verification_code=secrets.token_urlsafe(12),
    )
    db.add(doc)
    db.flush()
    return doc


def send_exit_pack(employee_id: int) -> None:
    """
    Email the leaver their NOC, payslip summary and tax certificate.

    Runs in a background task with its own session: the request that triggered
    it has already committed, and a mail failure must not roll back an exit
    that has otherwise completed.
    """
    from app.database import SessionLocal
    from app.utils.email import send_email, _email_shell, COMPANY_NAME

    db = SessionLocal()
    try:
        employee = db.query(User).filter(User.id == employee_id).first()
        if not employee or not employee.email:
            return
        payslips = (db.query(Payroll).filter(Payroll.employee_id == employee_id)
                    .order_by(Payroll.year, Payroll.month).all())
        total_tax = sum(p.tax or 0.0 for p in payslips)
        first = f"{payslips[0].month:02d}/{payslips[0].year}" if payslips else "-"
        last = f"{payslips[-1].month:02d}/{payslips[-1].year}" if payslips else "-"

        from app.models.settlement import FinalSettlement
        settlement = (db.query(FinalSettlement)
                      .filter(FinalSettlement.employee_id == employee_id)
                      .order_by(FinalSettlement.id.desc()).first())
        settlement_line = ""
        if settlement:
            settlement_line = (
                '<li><strong>Final settlement</strong> — '
                f'{settings.CURRENCY} {settlement.net_payable:,.0f} payable, '
                'itemised in the portal</li>')

        body = _email_shell(f"""
          <tr><td style="padding: 8px 40px 40px;">
            <h1 style="margin:24px 0 4px;color:#111827;font-size:22px;font-weight:700;">Your employment records</h1>
            <p style="margin:0 0 24px;color:#6B7280;font-size:14px;">Everything you may need after leaving.</p>
            <p style="margin:0 0 16px;color:#374151;font-size:16px;">Dear {employee.full_name},</p>
            <p style="margin:0 0 20px;color:#374151;font-size:15px;line-height:1.6;">
              Thank you for your time at {COMPANY_NAME}. Your records remain available
              in the portal, and are summarised here for your reference.</p>
            <ul style="margin:0 0 20px;padding-left:20px;color:#374151;font-size:15px;line-height:1.7;">
              <li><strong>No Objection Certificate</strong> — issued and verifiable online</li>
              <li><strong>Payslips</strong> — {len(payslips)} covering {first} to {last}</li>
              <li><strong>Tax certificate</strong> — total tax deducted
                  PKR {total_tax:,.0f}</li>
              {settlement_line}
            </ul>
            <p style="margin:0 0 20px;color:#374151;font-size:15px;line-height:1.6;">
              Please download anything you need. If you have any question about your
              final settlement or these records, reply to this email.</p>
            <p style="margin:0;color:#374151;font-size:15px;">We wish you well.</p>
          </td></tr>""")
        send_email([employee.email], f"Your employment records — {COMPANY_NAME}",
                   body, html=True, reply_to=settings.HR_REPLY_TO or settings.COMPANY_EMAIL)
    except Exception:
        logger.exception("Failed to send the exit pack for employee %s", employee_id)
    finally:
        db.close()


def settlement_ready(db: Session, resignation: Resignation) -> tuple:
    """
    Whether the final payment has been signed off. (ready, reason_if_not).

    An exit used to complete on clearance alone: the account closed and the
    exit pack went out while the last payment was still an unopened
    spreadsheet. The figures have to be agreed before the door shuts.
    """
    from app.models.settlement import FinalSettlement, SettlementStatusEnum

    s = (db.query(FinalSettlement)
         .filter(FinalSettlement.resignation_id == resignation.id).first())
    if s is None:
        return False, "No final settlement has been drafted for this exit."
    if s.status == SettlementStatusEnum.DRAFT:
        return False, "The final settlement is still a draft. Approve it first."
    return True, ""


def finalise_exit(db: Session, resignation: Resignation, actor: User,
                  background_tasks=None) -> Resignation:
    """
    Issue the NOC, deactivate the account and queue the exit pack.

    Deactivation happens here rather than at approval so the employee keeps
    access through their notice period. Their payroll history is untouched —
    payslips, leave and attendance all remain, because a leaver's records are
    exactly what gets asked for afterwards.
    """
    employee = resignation.employee or db.query(User).filter(
        User.id == resignation.employee_id).first()

    noc = issue_noc(db, resignation, actor)
    resignation.noc_document_id = noc.id
    resignation.status = ResignationStatusEnum.COMPLETED
    resignation.completed_at = datetime.utcnow()
    resignation.exit_pack_sent_at = datetime.utcnow()
    if employee:
        employee.is_active = False
    db.commit()

    if background_tasks is not None:
        background_tasks.add_task(send_exit_pack, resignation.employee_id)
    else:
        send_exit_pack(resignation.employee_id)
    return resignation


def complete_due_exits(db: Session) -> int:
    """
    Finalise every cleared resignation whose last working day has arrived.

    Called by the scheduled sweep. Idempotent: a resignation already completed
    is not in CLEARED and is therefore not picked up again. Anything whose
    settlement is not yet approved is reported back rather than finalised.
    """
    from datetime import date

    due = db.query(Resignation).filter(
        Resignation.status == ResignationStatusEnum.CLEARED,
    ).all()
    done, waiting = 0, []
    for r in due:
        last = r.agreed_last_working_day or r.proposed_last_working_day
        if not (last and last <= date.today()):
            continue
        ready, reason = settlement_ready(db, r)
        if not ready:
            # Reported rather than raised: this is a normal state on the day
            # someone leaves, and the sweep will pick it up as soon as HR
            # approves the figures. It surfaces in the scheduled-jobs panel.
            waiting.append({"resignation_id": r.id, "employee_id": r.employee_id,
                            "reason": reason})
            continue
        actor = db.query(User).filter(User.id == r.hr_cleared_by).first() \
            or db.query(User).filter(User.role == "ADMIN").first()
        try:
            finalise_exit(db, r, actor=actor)
            done += 1
        except Exception:
            db.rollback()
            logger.exception("Failed to finalise exit for resignation %s", r.id)
    return {"completed": done, "waiting_on_settlement": waiting}
