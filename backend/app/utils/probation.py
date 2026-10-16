"""
Probation: when it ends, who is told, and the letter that closes it.
"""
import logging
import secrets
from datetime import date, datetime, timedelta
from typing import List, Optional

from sqlalchemy.orm import Session

from app.models.document import (DocumentKindEnum, DocumentStatusEnum,
                                 DocumentTemplate, EmployeeDocument)
from app.models.notification import NotificationTypeEnum
from app.models.probation import ProbationStatusEnum
from app.models.user import User
from app.utils.documents import build_context, content_hash, render
from app.utils.leave_policy import LeavePolicyConfig
from app.utils.notifications import create_notification

logger = logging.getLogger(__name__)

# How long before the end date the line manager is asked to decide. Two weeks
# is enough to hold a conversation and still give notice if the answer is no.
NOTICE_DAYS = 14

# How far past an end date a case is still worth a notification. Beyond this it
# is history, not a decision anybody is about to make: switching this feature on
# finds every employee who was never formally confirmed, including people hired
# years ago, and mailing a manager about a probation that ended in 2024 teaches
# them to ignore the next one. Those cases stay in the /probation/due queue and
# are cleared with bulk-confirm.
BACKLOG_DAYS = 30

FALLBACK_CONFIRMATION = """
<h2>Confirmation of Employment</h2>
<p>Dear {{employee_name}},</p>
<p>Further to your appointment as <strong>{{designation}}</strong> with
{{company_name}} on {{date_of_joining}}, we are pleased to confirm that you
have successfully completed your probationary period with effect from
{{confirmation_date}}.</p>
<p>Your employment is now confirmed on the terms of your existing contract.
All other terms and conditions of your employment remain unchanged.</p>
<p>We thank you for your contribution so far and look forward to your continued
association with the Company.</p>
<p>{{signatory_name}}<br>{{signatory_designation}}<br>{{company_name}}</p>
"""


def effective_end_date(user: User) -> Optional[date]:
    """
    When this person's probation ends.

    The recorded date wins. Where there is none — every employee who predates
    this feature — fall back to the same window the leave rules have always
    used, so the two never disagree about who is on probation.
    """
    if getattr(user, "probation_end_date", None):
        return user.probation_end_date
    joined = getattr(user, "date_of_joining", None)
    if not joined:
        return None
    start = joined.date() if isinstance(joined, datetime) else joined
    return start + timedelta(days=LeavePolicyConfig.PROBATION_MONTHS * 30)


def current_status(user: User) -> str:
    """The status to show, computed for anyone who has never been reviewed."""
    recorded = getattr(user, "probation_status", None)
    if recorded:
        return recorded
    end = effective_end_date(user)
    if end and date.today() < end:
        return ProbationStatusEnum.IN_PROBATION.value
    # Past the window with no decision ever recorded. Not the same as
    # confirmed — nobody has said so — and the distinction is the whole point
    # of the reminder below.
    return ProbationStatusEnum.IN_PROBATION.value


def due_for_review(db: Session, within_days: int = NOTICE_DAYS) -> List[User]:
    """
    Everyone whose probation ends soon, or ended without anyone deciding.

    Overdue cases are deliberately included: an employee who quietly passed
    their end date six months ago is exactly who this is meant to surface.
    """
    horizon = date.today() + timedelta(days=within_days)
    out = []
    for user in db.query(User).filter(User.is_active.is_(True)).all():
        if current_status(user) not in (ProbationStatusEnum.IN_PROBATION.value,
                                        ProbationStatusEnum.EXTENDED.value):
            continue
        end = effective_end_date(user)
        if end and end <= horizon:
            out.append(user)
    return sorted(out, key=lambda u: effective_end_date(u) or date.max)


def send_probation_reminders(db: Session) -> dict:
    """
    Tell the line manager, and HR, that a decision is due.

    Sent once per probation period, not once a day for a fortnight — the
    marker on the employee is what makes the difference between a reminder and
    a nuisance. Never raises: a failed notification must not fail the sweep.
    """
    try:
        due = due_for_review(db)
        notified = 0
        privileged = [u for u in db.query(User).filter(User.is_active.is_(True)).all()
                      if u.role.value == "admin" or bool(getattr(u, "is_hr", False))]

        skipped_backlog = 0
        for employee in due:
            if employee.probation_notice_sent_at is not None:
                continue
            end = effective_end_date(employee)
            days = (end - date.today()).days if end else 0
            if days < -BACKLOG_DAYS:
                # Historical, not imminent. Visible in the queue, not mailed.
                skipped_backlog += 1
                continue
            when = (f"ends in {days} day{'s' if days != 1 else ''}" if days > 0
                    else ("ends today" if days == 0
                          else f"ended {abs(days)} day{'s' if abs(days) != 1 else ''} ago"))
            message = (f"{employee.full_name}'s probation {when} "
                       f"({end.strftime('%d %B %Y') if end else 'date not set'}). "
                       "Confirm, extend, or record that it was not confirmed.")

            recipients = {u.id for u in privileged}
            if employee.manager_id:
                recipients.add(employee.manager_id)
            for user_id in recipients:
                create_notification(db, user_id, "Probation decision due", message,
                                    NotificationTypeEnum.WARNING,
                                    related_id=employee.id)
            employee.probation_notice_sent_at = datetime.utcnow()
            notified += 1

        db.commit()
        return {"due": len(due), "notified": notified,
                "backlog_not_notified": skipped_backlog}
    except Exception:
        db.rollback()
        logger.exception("Probation reminder sweep failed")
        return {"due": 0, "notified": 0, "backlog_not_notified": 0, "error": True}


def issue_confirmation_letter(db: Session, employee: User, actor: User,
                              effective: date) -> EmployeeDocument:
    """
    Issue the confirmation letter through the same machinery as every other
    document: rendered from a template HR can edit, hashed, and given a
    verification code.
    """
    template = (db.query(DocumentTemplate)
                .filter(DocumentTemplate.kind == DocumentKindEnum.CONFIRMATION,
                        DocumentTemplate.is_active.is_(True))
                .order_by(DocumentTemplate.version.desc()).first())

    ctx = build_context(db, employee, extra={
        "confirmation_date": effective.strftime("%d %B %Y"),
        "signatory_name": actor.full_name,
        "signatory_designation": actor.designation or "",
    })
    body = render(template.body_html if template else FALLBACK_CONFIRMATION, ctx)

    doc = EmployeeDocument(
        employee_id=employee.id,
        template_id=template.id if template else None,
        template_version=template.version if template else None,
        kind=DocumentKindEnum.CONFIRMATION,
        title=template.title if template else "Confirmation of Employment",
        body_html=body, content_hash=content_hash(body),
        # Issued by the company. Nothing for the employee to sign — this is
        # notice of a decision, not an agreement to be struck.
        requires_signature=False, status=DocumentStatusEnum.SIGNED,
        issued_by=actor.id, verification_code=secrets.token_urlsafe(12),
    )
    db.add(doc)
    db.flush()
    return doc
