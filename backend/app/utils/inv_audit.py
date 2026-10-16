"""
Cycle counting: rolling counts weighted by value, instead of an annual
stocktake nobody ever gets round to.

A session scopes a location or a category, lists what the register expects to
find there, and records what was actually found. Anything not scanned by the
time the session closes becomes a variance with a note against it.
"""
import logging
from datetime import datetime
from typing import List, Optional

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.config import settings
from app.models.inventory import (AuditOutcomeEnum, AuditSessionStatusEnum,
                                  CategoryDomainEnum, InvCategory,
                                  InvAuditLine, InvAuditSession, InvItem,
                                  InvLocation, ItemStatusEnum,
                                  TrackingTypeEnum)
from app.models.user import User
from app.utils import inv_movements

logger = logging.getLogger(__name__)

# Statuses that are not expected to be on a shelf and so are never counted.
_OUT_OF_SCOPE = {ItemStatusEnum.SCRAPPED.value, ItemStatusEnum.LOST.value,
                 ItemStatusEnum.IN_TRANSIT.value, ItemStatusEnum.WITH_CLIENT.value,
                 ItemStatusEnum.UNDER_REPAIR.value}


def _bad(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail)


def _descendant_location_ids(db: Session, root_id: int) -> List[int]:
    """Every location at or under this node. A rack means its shelves too."""
    ids, frontier, seen = [root_id], [root_id], {root_id}
    while frontier:
        children = (db.query(InvLocation.id)
                    .filter(InvLocation.parent_id.in_(frontier)).all())
        frontier = [c.id for c in children if c.id not in seen]
        seen.update(frontier)
        ids.extend(frontier)
    return ids


def _counts_office(db: Session, user: Optional[User]) -> bool:
    """Office equipment is admin and HR's; the lab custodian counts the lab."""
    from app.api.deps import is_privileged
    return user is not None and is_privileged(user, db)


def expected_items(db: Session, session_row: InvAuditSession) -> List[InvItem]:
    """What the register says should be findable within this session's scope."""
    q = db.query(InvItem).filter(~InvItem.status.in_(_OUT_OF_SCOPE))
    if not _counts_office(db, session_row.opener):
        # A lab count never expects a laptop, so it can never mark one lost.
        q = q.join(InvCategory, InvItem.category_id == InvCategory.id).filter(
            InvCategory.domain != CategoryDomainEnum.OFFICE.value)
    if session_row.scope_location_id:
        q = q.filter(InvItem.current_location_id.in_(
            _descendant_location_ids(db, session_row.scope_location_id)))
    if session_row.scope_category_id:
        q = q.filter(InvItem.category_id == session_row.scope_category_id)
    return q.order_by(InvItem.tag).all()


def open_session(db: Session, actor: User, *, location_id: Optional[int] = None,
                 category_id: Optional[int] = None,
                 label: Optional[str] = None) -> InvAuditSession:
    """
    Start a count, and snapshot what is expected at the moment it starts.

    The lines are written up front rather than worked out at close, so the
    expected figures are the ones that were true when the custodian walked to
    the rack — not ones that moved while they were counting.
    """
    if not location_id and not category_id:
        raise _bad("An audit session needs a location or a category to scope it.")

    row = InvAuditSession(
        scope_location_id=location_id, scope_category_id=category_id,
        label=label, opened_by=actor.id,
        status=AuditSessionStatusEnum.OPEN.value)
    db.add(row)
    db.flush()

    for item in expected_items(db, row):
        db.add(InvAuditLine(session_id=row.id, item_id=item.id,
                            expected_qty=float(item.qty or 0.0)))
    db.commit()
    db.refresh(row)
    return row


def scan(db: Session, session_row: InvAuditSession, actor: User, *, tag: str,
         counted_qty: Optional[float] = None,
         note: Optional[str] = None) -> InvAuditLine:
    """
    Record one tag as present.

    A tag that was not expected here is added to the session as a line with a
    zero expectation — finding something on the wrong shelf is a result, not an
    error, and it is the commonest one.
    """
    if session_row.status != AuditSessionStatusEnum.OPEN.value:
        raise _bad("This audit session is closed.")

    item = db.query(InvItem).filter(InvItem.tag == tag.strip()).first()
    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail=f"No item carries the tag '{tag}'.")
    if item.is_office and not _counts_office(db, actor):
        raise _bad(f"{item.tag} is office equipment and is not part of a lab count.")

    line = (db.query(InvAuditLine)
            .filter(InvAuditLine.session_id == session_row.id,
                    InvAuditLine.item_id == item.id).first())
    if line is None:
        line = InvAuditLine(session_id=session_row.id, item_id=item.id,
                            expected_qty=0.0)
        db.add(line)

    line.counted_qty = counted_qty if counted_qty is not None else float(item.qty or 1.0)
    line.variance = round(float(line.counted_qty) - float(line.expected_qty or 0.0), 4)
    line.variance_value = round(abs(line.variance) * item.variance_unit_value, 2)
    line.outcome = (AuditOutcomeEnum.PRESENT.value if line.expected_qty
                    else AuditOutcomeEnum.FOUND_ELSEWHERE.value)
    line.note = note
    line.scanned_at = datetime.utcnow()
    db.commit()
    db.refresh(line)
    return line


def within_tolerance(item: InvItem, line: InvAuditLine) -> bool:
    """
    Class C only: a small drift on passives is noted and ignored.

    Chasing an exact count on a reel of resistors costs more labour than the
    resistors, and a process that demands it is a process people quietly stop
    following.
    """
    if item.tracking_type != TrackingTypeEnum.QUANTITY.value:
        return False
    expected = float(line.expected_qty or 0.0)
    if expected <= 0:
        return False
    return abs(line.variance) / expected * 100.0 <= settings.INV_STOCK_TOLERANCE_PCT


def _previously_not_located(db: Session, session_row: InvAuditSession,
                            item_id: int) -> bool:
    """
    Whether the last closed session that covered this item also failed to find
    it. Two strikes and it is recorded as lost.
    """
    previous = (db.query(InvAuditLine)
                .join(InvAuditSession,
                      InvAuditLine.session_id == InvAuditSession.id)
                .filter(InvAuditLine.item_id == item_id,
                        InvAuditSession.status == AuditSessionStatusEnum.CLOSED.value,
                        InvAuditSession.id != session_row.id)
                .order_by(InvAuditSession.closed_at.desc())
                .first())
    return bool(previous and previous.outcome == AuditOutcomeEnum.NOT_LOCATED.value)


def close_session(db: Session, session_row: InvAuditSession, actor: User, *,
                  notes: Optional[str] = None) -> dict:
    """
    Close the count and turn everything unscanned into a variance.

    An item held by a person is not missing — it is exactly where the register
    says it is, just not on the shelf — so those close as `with_person` rather
    than counting against the rack. Anything genuinely not located for a second
    consecutive session gets a `lost` movement written against it, which is the
    two-strike rule from section 12.
    """
    if session_row.status != AuditSessionStatusEnum.OPEN.value:
        raise _bad("This audit session is already closed.")

    variances, variance_value, newly_lost = 0, 0.0, []

    for line in session_row.lines:
        item = line.item
        if line.scanned_at is not None:
            if line.variance and not within_tolerance(item, line):
                variances += 1
                variance_value += float(line.variance_value or 0.0)
            continue

        # Never scanned. Why not?
        if item and item.current_holder_id:
            line.outcome = AuditOutcomeEnum.WITH_PERSON.value
            line.counted_qty = float(line.expected_qty or 0.0)
            line.variance, line.variance_value = 0.0, 0.0
            line.note = line.note or (
                f"Held by {item.holder.full_name}" if item.holder else "Held by a person")
            continue

        line.outcome = AuditOutcomeEnum.NOT_LOCATED.value
        line.counted_qty = 0.0
        line.variance = -float(line.expected_qty or 0.0)
        line.variance_value = round(abs(line.variance) * item.variance_unit_value, 2) \
            if item else 0.0
        if not (item and within_tolerance(item, line)):
            variances += 1
            variance_value += float(line.variance_value or 0.0)

        if item and _previously_not_located(db, session_row, item.id):
            newly_lost.append(item)

    session_row.status = AuditSessionStatusEnum.CLOSED.value
    session_row.closed_at = datetime.utcnow()
    session_row.variance_count = variances
    session_row.variance_value = round(variance_value, 2)
    if notes:
        session_row.notes = notes
    db.commit()

    # Written after the close so the session is already a closed record if one
    # of these fails; a half-closed session is worse than a missing `lost` row.
    for item in newly_lost:
        try:
            inv_movements.mark_lost(
                db, item, actor,
                reason=f"Not located in two consecutive counts "
                       f"(session {session_row.id}).")
        except Exception:
            db.rollback()
            logger.exception("Could not mark item %s lost after session %s",
                             item.id, session_row.id)

    return {"session_id": session_row.id, "variances": variances,
            "variance_value": session_row.variance_value,
            "marked_lost": [i.tag for i in newly_lost]}
