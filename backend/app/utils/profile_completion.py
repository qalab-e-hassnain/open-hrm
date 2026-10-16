"""
Whether an employee's record is complete enough for the company to do its job.

Not a tidiness exercise. Every field below is here because something concrete
breaks without it: payroll cannot pay somebody with no bank account, a contract
cannot be issued without a CNIC, and nobody can be called if there is no
emergency contact. The distinction between required and recommended is exactly
that — required means something is blocked, recommended means something is
merely poorer.
"""
from datetime import datetime, timedelta
from typing import List, Optional

from sqlalchemy.orm import Session

# (attribute, label shown to the employee, why it is being asked for)
REQUIRED_FIELDS = [
    ("phone", "Mobile number", "How the office reaches you."),
    ("date_of_birth", "Date of birth", "Needed on your contract and for records."),
    ("cnic_number", "CNIC number",
     "Printed on your contract, payslips and tax certificate."),
    ("father_name", "Father's name", "Required on formal documents in Pakistan."),
    ("address", "Home address", "Used on your contract and for correspondence."),
    ("emergency_contact_name", "Emergency contact — name",
     "Who we call if something happens at work."),
    ("emergency_contact_phone", "Emergency contact — number",
     "Who we call if something happens at work."),
    ("account_title", "Bank account title", "Salary cannot be transferred without it."),
    ("account_number", "Bank account number / IBAN",
     "Salary cannot be transferred without it."),
    ("bank_number", "Bank name", "Salary cannot be transferred without it."),
]

RECOMMENDED_FIELDS = [
    ("profile_picture_url", "Profile photo",
     "Appears on the org chart and the team directory."),
    ("cnic_front_url", "CNIC — front", "Kept on file with your contract."),
    ("cnic_back_url", "CNIC — back", "Kept on file with your contract."),
    ("personal_email", "Personal email",
     "So we can still reach you after your work account closes."),
    ("linkedin_welcome_picture_url", "LinkedIn welcome photo",
     "Used for your welcome post."),
    ("marital_status", "Marital status", "Used for benefits and records."),
    ("branch_code", "Bank branch code", "Speeds up the salary transfer."),
]

# How often somebody with an incomplete profile is reminded. Two weeks: often
# enough not to be forgotten, rare enough not to become noise people learn to
# dismiss without reading.
REMINDER_INTERVAL_DAYS = 14


def _missing(user, fields) -> List[dict]:
    out = []
    for attr, label, why in fields:
        value = getattr(user, attr, None)
        if value is None or (isinstance(value, str) and not value.strip()):
            out.append({"field": attr, "label": label, "why": why})
    return out


def evaluate(user) -> dict:
    """
    What is missing from this person's record, and how far along they are.

    Cheap enough to compute on every response — pure attribute checks, no
    queries — which is what lets it ride along on the login payload rather than
    needing a call of its own.
    """
    missing_required = _missing(user, REQUIRED_FIELDS)
    missing_recommended = _missing(user, RECOMMENDED_FIELDS)

    total = len(REQUIRED_FIELDS) + len(RECOMMENDED_FIELDS)
    filled = total - len(missing_required) - len(missing_recommended)
    return {
        "is_complete": not missing_required,
        "percent": round(100.0 * filled / total) if total else 100,
        "required_total": len(REQUIRED_FIELDS),
        "required_missing": len(missing_required),
        "missing_required": missing_required,
        "missing_recommended": missing_recommended,
        # The frontend's cue to put the completion screen in front of somebody
        # before anything else. A prompt, not a lockout: an employee with a half
        # -filled record still needs to be able to book leave and read a payslip.
        "prompt_on_login": bool(missing_required),
    }


def send_profile_reminders(db: Session) -> dict:
    """
    Ask the people with gaps to fill them in.

    Repeats on an interval rather than once, because a profile stays incomplete
    until somebody acts and a single notification months ago is not a prompt.
    Never raises: a failed notification must not fail the sweep.
    """
    import logging

    from app.models.notification import NotificationTypeEnum
    from app.models.user import User
    from app.utils.notifications import create_notification

    logger = logging.getLogger(__name__)
    try:
        now = datetime.utcnow()
        cutoff = now - timedelta(days=REMINDER_INTERVAL_DAYS)
        notified, complete = 0, 0

        for user in db.query(User).filter(User.is_active.is_(True)).all():
            state = evaluate(user)
            if state["is_complete"]:
                complete += 1
                # Clear the marker so a field emptied later starts the cycle
                # again rather than being silently tolerated forever.
                user.profile_reminder_sent_at = None
                continue

            last = user.profile_reminder_sent_at
            if last is not None and last > cutoff:
                continue

            missing = state["missing_required"]
            named = ", ".join(m["label"] for m in missing[:4])
            more = f" and {len(missing) - 4} more" if len(missing) > 4 else ""
            create_notification(
                db, user.id, "Your profile is incomplete",
                f"{len(missing)} thing{'s' if len(missing) != 1 else ''} still "
                f"needed: {named}{more}. Salary transfers, your contract and your "
                "payslips all depend on these, so please fill them in.",
                NotificationTypeEnum.WARNING, related_id=user.id)
            user.profile_reminder_sent_at = now
            notified += 1

        db.commit()
        return {"notified": notified, "already_complete": complete}
    except Exception:
        db.rollback()
        logger.exception("Profile completion reminder sweep failed")
        return {"notified": 0, "already_complete": 0, "error": True}
