"""
Daily admin reminder sweep for leave requests that need a decision.

Two cases, both aimed at the same problem — a request sitting undecided
because nobody noticed it in time:

* "Overdue" — still PENDING/MANAGER_APPROVED and the requested start date has
  already passed. Re-sent once per day (via overdue_reminder_sent_at) for as
  long as it stays undecided, since this is the more urgent case.
* "Upcoming" — still undecided and the start date is tomorrow. Sent once
  (via upcoming_reminder_sent_at) as an early heads-up.

Triggered by a real Vercel Cron Job (see app/api/v1/cron.py), not the lazy
"runs when an admin opens the app" pattern used for birthday/anniversary
notices — a reminder that only fires when someone is already looking isn't
much of a reminder.
"""
import logging
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import or_
from sqlalchemy.orm import Session, joinedload

from app.models.leave import Leave, LeaveStatusEnum
from app.models.user import RoleEnum, User
from app.utils.email import send_leave_reminder_email

logger = logging.getLogger(__name__)

_UNDECIDED = ("pending", "manager_approved")


def _status_value(leave: Leave) -> str:
    st = leave.status
    return st.value if hasattr(st, "value") else str(st).split(".")[-1].lower()


def _admin_emails(db: Session) -> list[str]:
    rows = db.query(User.email).filter(
        or_(User.role == RoleEnum.ADMIN, User.is_hr == True),  # noqa: E712
        User.is_active == True,  # noqa: E712
    ).all()
    return [r[0] for r in rows if r[0]]


def send_leave_reminders(db: Session, today: Optional[datetime] = None) -> dict:
    """Run the sweep once. Returns counts for the cron endpoint to report.

    Never raises — a reminder failing to send must not turn into a 500 that
    makes Vercel treat the cron invocation as failed and retry it into a
    duplicate-email loop.
    """
    now = today or datetime.utcnow()
    today_date = now.date()
    tomorrow_date = today_date + timedelta(days=1)

    try:
        # Everything up to and including tomorrow: overdue candidates can be
        # any age, upcoming candidates are exactly tomorrow. The status check
        # below does the real filtering (only PENDING/MANAGER_APPROVED
        # survive), this just keeps the query from scanning decided leaves
        # that start further in the future.
        upper_bound = datetime.combine(tomorrow_date + timedelta(days=1), datetime.min.time())
        candidates = (
            db.query(Leave)
            .options(joinedload(Leave.employee))
            .filter(Leave.start_date < upper_bound)
            .all()
        )

        overdue, upcoming = [], []
        for leave in candidates:
            if _status_value(leave) not in _UNDECIDED:
                continue
            start = leave.start_date.date()
            employee_name = leave.employee.full_name if leave.employee else f"Employee #{leave.employee_id}"
            leave_type = leave.leave_type.value if hasattr(leave.leave_type, "value") else str(leave.leave_type)

            if start < today_date:
                last_sent = leave.overdue_reminder_sent_at
                if last_sent is not None and last_sent.date() >= today_date:
                    continue
                overdue.append({
                    "employee_name": employee_name,
                    "leave_type": leave_type,
                    "start_date": start,
                    "end_date": leave.end_date.date() if leave.end_date else start,
                    "days_overdue": (today_date - start).days,
                })
                leave.overdue_reminder_sent_at = now
            elif start == tomorrow_date:
                if leave.upcoming_reminder_sent_at is not None:
                    continue
                upcoming.append({
                    "employee_name": employee_name,
                    "leave_type": leave_type,
                    "start_date": start,
                    "end_date": leave.end_date.date() if leave.end_date else start,
                })
                leave.upcoming_reminder_sent_at = now

        recipient_count = 0
        if overdue or upcoming:
            recipients = _admin_emails(db)
            recipient_count = len(recipients)
            if recipients:
                send_leave_reminder_email(recipients, overdue, upcoming)
            else:
                logger.warning("Leave reminder sweep found %d overdue / %d upcoming but no admin/HR recipients exist", len(overdue), len(upcoming))

        db.commit()
        return {"overdue": len(overdue), "upcoming": len(upcoming), "recipients": recipient_count}
    except Exception:
        db.rollback()
        logger.exception("Leave reminder sweep failed")
        return {"overdue": 0, "upcoming": 0, "recipients": 0, "error": True}
