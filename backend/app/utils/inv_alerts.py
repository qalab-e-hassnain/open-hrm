"""
Inventory alerts, batched into one digest per person.

Section 13 is explicit about this: "Alerts batch into a single daily digest per
person rather than firing individually. Individual alerts train people to
ignore the channel." So there is one sweep rather than one per alert type, and
it recomputes everything outstanding on each run instead of remembering what it
sent — which also makes it safe to run late, or twice, which on this stack it
will be.

A note on timing. There is no scheduler here; the sweeps ride on the portal's
own traffic (see app/utils/scheduler.py). "Three days before expiry" therefore
means "on the first sweep at or after three days before". Every alert below
tolerates that, because each one is derived from current state rather than from
a calendar.
"""
import logging
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Dict, List

from sqlalchemy import or_
from sqlalchemy.orm import Session, joinedload

from app.config import settings
from app.models.inventory import (InvItem, InvRequest, ItemStatusEnum,
                                  LocationTypeEnum, RequestStatusEnum,
                                  TrackingTypeEnum)
from app.models.leave import Leave, LeaveStatusEnum
from app.models.notification import NotificationTypeEnum
from app.models.user import RoleEnum, User
from app.utils.notifications import create_notification

logger = logging.getLogger(__name__)


class Digest:
    """What one person is told in one email."""

    def __init__(self, user: User):
        self.user = user
        self.sections: Dict[str, List[str]] = defaultdict(list)

    def add(self, section: str, line: str) -> None:
        self.sections[section].append(line)

    @property
    def line_count(self) -> int:
        return sum(len(v) for v in self.sections.values())


def _custodian_recipients(db: Session) -> List[User]:
    """
    Whoever should hear about the store itself.

    The custodian if one has been appointed; admins as well, so the alerts do
    not go nowhere in the window before that appointment is made — which, per
    the design's own risk table, is a window that tends to be longer than
    anyone plans.
    """
    return (db.query(User)
            .filter(User.is_active == True,                     # noqa: E712
                    or_(User.is_lab_custodian == True,          # noqa: E712
                        User.role == RoleEnum.ADMIN))
            .all())


def collect(db: Session) -> Dict[int, Digest]:
    """Everything outstanding right now, grouped by who needs to hear it."""
    today = date.today()
    digests: Dict[int, Digest] = {}

    def digest_for(user: User) -> Digest:
        if user.id not in digests:
            digests[user.id] = Digest(user)
        return digests[user.id]

    custodians = _custodian_recipients(db)

    # --- Holds: approaching, and overdue ----------------------------------
    held = (db.query(InvItem)
            .options(joinedload(InvItem.holder))
            .filter(InvItem.current_holder_id.isnot(None),
                    InvItem.hold_due_date.isnot(None))
            .all())
    warn_from = today + timedelta(days=settings.INV_HOLD_WARN_DAYS)
    for item in held:
        holder = item.holder
        if not holder or not holder.is_active:
            continue
        if item.hold_due_date < today:
            days = (today - item.hold_due_date).days
            line = (f"{item.name} ({item.tag}) — {days} day{'s' if days != 1 else ''} "
                    f"overdue, due {item.hold_due_date:%d %b}")
            digest_for(holder).add("Overdue — please return or renew", line)
            # The lead hears about their own team's overdue holds, not the
            # whole lab's.
            if holder.manager_id:
                lead = db.query(User).filter(User.id == holder.manager_id,
                                             User.is_active == True).first()  # noqa: E712
                if lead:
                    digest_for(lead).add(
                        "Your team's overdue equipment",
                        f"{holder.full_name}: {item.name} ({item.tag}), {days} days overdue")
        elif item.hold_due_date <= warn_from:
            days = (item.hold_due_date - today).days
            digest_for(holder).add(
                "Due back soon",
                f"{item.name} ({item.tag}) — due in {days} day{'s' if days != 1 else ''} "
                f"on {item.hold_due_date:%d %b}. One tap renews it.")

    # --- Stock below its reorder point ------------------------------------
    low = (db.query(InvItem)
           .filter(InvItem.min_qty.isnot(None), InvItem.qty <= InvItem.min_qty,
                   InvItem.status == ItemStatusEnum.IN_STOCK.value)
           .all())
    for item in low:
        for person in custodians:
            digest_for(person).add(
                "Stock below minimum",
                f"{item.name} ({item.tag}) — {item.qty:g} left, minimum {item.min_qty:g}")

    # --- Quarantine sitting too long --------------------------------------
    cutoff = datetime.utcnow() - timedelta(days=settings.INV_QUARANTINE_DAYS)
    stuck = (db.query(InvItem)
             .filter(InvItem.quarantined_at.isnot(None),
                     InvItem.quarantined_at < cutoff)
             .all())
    for item in stuck:
        days = (datetime.utcnow() - item.quarantined_at).days
        for person in custodians:
            digest_for(person).add(
                "In quarantine too long",
                f"{item.name} ({item.tag}) — {days} days in quarantine")

    # --- Requests waiting on an approval ----------------------------------
    stale = datetime.utcnow() - timedelta(hours=48)
    pending = (db.query(InvRequest)
               .options(joinedload(InvRequest.requester))
               .filter(InvRequest.status == RequestStatusEnum.PENDING.value,
                       InvRequest.created_at < stale)
               .all())
    for req in pending:
        requester = req.requester
        approvers = list(custodians)
        if requester and requester.manager_id:
            lead = db.query(User).filter(User.id == requester.manager_id,
                                         User.is_active == True).first()  # noqa: E712
            if lead:
                approvers.append(lead)
        what = req.item.name if req.item else (
            req.category.name if req.category else "an item")
        for person in approvers:
            digest_for(person).add(
                "Requests waiting on you",
                f"{requester.full_name if requester else 'Someone'} asked for "
                f"{req.qty:g} x {what} — raised {req.created_at:%d %b}")

    # --- Someone on long leave still holding things -----------------------
    horizon = today + timedelta(days=settings.INV_LONG_LEAVE_DAYS)
    long_leaves = (db.query(Leave)
                   .filter(Leave.status == LeaveStatusEnum.APPROVED,
                           Leave.end_date >= today,
                           Leave.start_date <= horizon)
                   .all())
    for leave in long_leaves:
        span = (leave.end_date - leave.start_date).days + 1
        if span <= settings.INV_LONG_LEAVE_DAYS:
            continue
        # Office equipment goes on leave with its holder; that is what it is for.
        holdings = [i for i in (db.query(InvItem)
                                .filter(InvItem.current_holder_id == leave.employee_id)
                                .all()) if not i.is_office]
        if not holdings:
            continue
        employee = db.query(User).filter(User.id == leave.employee_id).first()
        tags = ", ".join(f"{i.name} ({i.tag})" for i in holdings[:5])
        if len(holdings) > 5:
            tags += f", and {len(holdings) - 5} more"
        for person in custodians:
            digest_for(person).add(
                "On long leave, still holding equipment",
                f"{employee.full_name if employee else 'An employee'} is away "
                f"{leave.start_date:%d %b}–{leave.end_date:%d %b} ({span} days) "
                f"holding {tags}")

    # --- Clearance outstanding on an exit in progress ---------------------
    from app.models.lifecycle import Resignation, ResignationStatusEnum

    leaving = (db.query(Resignation)
               .filter(Resignation.status.in_([
                   ResignationStatusEnum.CEO_APPROVED,
                   ResignationStatusEnum.CLEARED]))
               .all())
    for r in leaving:
        holdings = (db.query(InvItem)
                    .filter(InvItem.current_holder_id == r.employee_id).all())
        if not holdings:
            continue
        employee = db.query(User).filter(User.id == r.employee_id).first()
        last_day = r.agreed_last_working_day or r.proposed_last_working_day
        for person in custodians:
            # The lab custodian chases lab kit; office equipment is admin's.
            mine = [i for i in holdings
                    if person.role == RoleEnum.ADMIN or not i.is_office]
            if not mine:
                continue
            digest_for(person).add(
                "Clearance outstanding",
                f"{employee.full_name if employee else 'A leaver'} leaves "
                f"{last_day:%d %b} still holding {len(mine)} item(s): "
                + ", ".join(i.tag for i in mine[:6]))

    return digests


def _digest_html(digest: Digest) -> str:
    from app.utils.email import _email_shell

    blocks = []
    for section, lines in digest.sections.items():
        items = "".join(
            f'<li style="margin:0 0 6px;">{line}</li>' for line in lines)
        blocks.append(
            f'<h2 style="margin:22px 0 8px;color:#111827;font-size:15px;'
            f'font-weight:700;">{section}</h2>'
            f'<ul style="margin:0;padding-left:20px;color:#374151;font-size:14px;'
            f'line-height:1.7;">{items}</ul>')

    return _email_shell(f"""
      <tr><td style="padding: 8px 40px 40px;">
        <h1 style="margin:24px 0 4px;color:#111827;font-size:22px;font-weight:700;">Lab inventory</h1>
        <p style="margin:0 0 20px;color:#6B7280;font-size:14px;">
          Everything needing your attention today, in one message.</p>
        <p style="margin:0 0 4px;color:#374151;font-size:16px;">Dear {digest.user.full_name},</p>
        {''.join(blocks)}
        <p style="margin:24px 0 0;color:#6B7280;font-size:13px;">
          Open the portal to act on any of these.</p>
      </td></tr>""")


def send_digests(db: Session) -> dict:
    """
    One email and one in-app notification per person with anything outstanding.

    Registered as the `inventory_digest` sweep. Idempotent by construction:
    it derives everything from current state, so running it twice in a day
    sends the same picture twice rather than double-counting anything.
    """
    from app.utils.email import send_email

    digests = collect(db)
    sent, failed, lines = 0, 0, 0

    for digest in digests.values():
        lines += digest.line_count
        summary = "; ".join(
            f"{name} ({len(rows)})" for name, rows in digest.sections.items())
        try:
            create_notification(
                db, digest.user.id, "Lab inventory — items needing attention",
                summary, NotificationTypeEnum.WARNING,
                related_type="inventory_item")
        except Exception:
            db.rollback()
            logger.exception("Could not write the inventory notification for %s",
                             digest.user.id)

        if not digest.user.email:
            continue
        try:
            send_email([digest.user.email], "Lab inventory — your daily digest",
                       _digest_html(digest), html=True)
            sent += 1
        except Exception:
            failed += 1
            logger.exception("Could not email the inventory digest to %s",
                             digest.user.email)

    return {"recipients": len(digests), "emails_sent": sent,
            "emails_failed": failed, "alert_lines": lines}
