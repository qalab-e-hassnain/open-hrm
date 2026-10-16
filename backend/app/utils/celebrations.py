"""
Birthday and work-anniversary greetings for admins and HR.

There is no scheduler in this stack (see utils/attendance_session.py for the
same constraint), so the sweep runs lazily: any admin or HR manager reading
their notifications triggers it first. A per-user year marker makes it
idempotent, so a hundred reads on the same day still produce one notification
per person per year.

Deliberately limited to the people who act on it — admins and HR — rather than
announcing a birthday company-wide, which is a decision for the business, not
for a background job.
"""
from datetime import date, datetime
from typing import List, Optional

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models.notification import NotificationTypeEnum
from app.models.user import RoleEnum, User
from app.utils.notifications import notify_users


def _same_day(moment: Optional[datetime], today: date) -> bool:
    """True when a stored date falls on today's month and day, any year."""
    if not moment:
        return False
    return moment.month == today.month and moment.day == today.day


def _ordinal(count: int) -> str:
    if 10 <= count % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(count % 10, "th")
    return f"{count}{suffix}"


def _recipient_ids(db: Session) -> List[int]:
    """Active admins and HR managers — the people who act on a greeting."""
    return [
        row[0]
        for row in db.query(User.id).filter(
            or_(User.role == RoleEnum.ADMIN, User.is_hr == True),  # noqa: E712
            User.is_active == True,  # noqa: E712
        ).all()
    ]


def sweep_celebrations(db: Session, today: Optional[date] = None) -> int:
    """
    Notify admins and HR of today's birthdays and anniversaries.

    Returns the number of greetings sent. Never raises: a failed greeting must
    not break the notification list the caller actually asked for.
    """
    today = today or datetime.utcnow().date()

    try:
        people = db.query(User).filter(User.is_active == True).all()  # noqa: E712
        due = [
            (person, kind)
            for person in people
            for kind in ("birthday", "anniversary")
            if _is_due(person, kind, today)
        ]
        if not due:
            return 0

        recipients = _recipient_ids(db)
        if not recipients:
            return 0

        sent = 0
        for person, kind in due:
            if kind == "birthday":
                years = today.year - person.date_of_birth.year
                title = f"🎂 {person.full_name}'s birthday is today"
                message = (
                    f"{person.full_name}"
                    f"{f' ({person.designation})' if person.designation else ''} turns {years} today."
                )
                person.birthday_notice_year = today.year
            else:
                years = today.year - person.anniversary_date.year
                title = f"💍 {person.full_name}'s wedding anniversary is today"
                message = (
                    f"{person.full_name}"
                    f"{f' ({person.designation})' if person.designation else ''} "
                    f"celebrates their {_ordinal(years)} wedding anniversary today."
                )
                person.anniversary_notice_year = today.year

            notify_users(
                db=db,
                user_ids=recipients,
                title=title,
                message=message,
                notification_type=NotificationTypeEnum.INFO,
                # The greeting is about that employee, so it links to them.
                related_id=person.id,
            )
            sent += 1

        # notify_users commits the notifications; this persists the markers that
        # stop the same greeting going out again later today.
        db.commit()
        return sent
    except Exception as exc:  # pragma: no cover - a greeting is never critical
        print(f"⚠️ Failed to sweep celebrations: {exc}")
        db.rollback()
        return 0


def _is_due(person: User, kind: str, today: date) -> bool:
    """A celebration falling today that has not been announced this year."""
    if kind == "birthday":
        return (
            _same_day(person.date_of_birth, today)
            and person.birthday_notice_year != today.year
        )
    # An anniversary is only meaningful while the employee is married.
    return (
        str(person.marital_status or "").strip().lower() == "married"
        and _same_day(person.anniversary_date, today)
        and person.anniversary_notice_year != today.year
    )
