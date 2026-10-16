"""
The only writer of `inv_movements`.

Every custody change in the system funnels through here. That is deliberate: a
movement row and the item's derived columns (`current_holder_id`,
`current_location_id`, `status`, `qty`, `hold_due_date`) have to move together
or the register starts disagreeing with its own history, and one writer is the
only way to keep that promise as movement types get added.

Endpoints validate who may do a thing and then call one of these. They never
construct an InvMovement themselves.
"""
import logging
from datetime import date, datetime, timedelta
from typing import Optional

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.config import settings
from app.models.inventory import (ConditionEnum, InvItem, InvLocation,
                                  InvMovement, ItemStatusEnum,
                                  LifecycleStateEnum, LocationTypeEnum,
                                  MovementTypeEnum, TrackingTypeEnum)
from app.models.notification import NotificationTypeEnum
from app.models.user import User
from app.utils.notifications import create_notification

logger = logging.getLogger(__name__)

# Statuses where the item is out of circulation and legitimately has neither a
# holder nor a shelf. Everything else must have exactly one.
_UNPLACED = {ItemStatusEnum.SCRAPPED.value, ItemStatusEnum.LOST.value,
             ItemStatusEnum.IN_TRANSIT.value}

# An item in one of these cannot be handed to somebody.
_UNISSUABLE = {ItemStatusEnum.SCRAPPED.value, ItemStatusEnum.LOST.value,
               ItemStatusEnum.UNDER_REPAIR.value, ItemStatusEnum.WITH_CLIENT.value}


def _bad(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail)


def assert_placed(item: InvItem) -> None:
    """
    The invariant from section 6: exactly one of holder or location, never
    both, never neither.

    Postgres has a CHECK that says the same thing and is the real guarantee.
    This exists so the rule also holds on the SQLite the tests run against, and
    so a violation surfaces as a clear message rather than an integrity error
    from the driver.
    """
    if item.status in _UNPLACED:
        return
    placed = (item.current_location_id is not None) + (item.current_holder_id is not None)
    if placed != 1:
        raise _bad(
            f"{item.tag} must be either on a shelf or with a person, not "
            f"{'both' if placed == 2 else 'neither'}.")


def hold_days_for(item: InvItem) -> Optional[int]:
    """
    How long this may stay out before it has to be re-confirmed.

    Consumable-grade things come back sooner than instruments, because a dev
    kit on a bench for a fortnight is work in progress and a reel of headers on
    a bench for a fortnight is lost. Office equipment has no hold at all: a
    laptop is issued for as long as the job lasts, and comes back at the exit.
    """
    if item.is_office:
        return None
    if item.is_quantity_tracked:
        return settings.INV_HOLD_DAYS_CONSUMABLE
    return settings.INV_HOLD_DAYS_ASSET


def _due(item: InvItem, expected_return_date: Optional[date]) -> Optional[date]:
    if item.is_office:
        return None
    if expected_return_date:
        return expected_return_date
    days = hold_days_for(item)
    return date.today() + timedelta(days=days) if days else None


def _until(due: Optional[date]) -> str:
    return f" until {due:%d %b %Y}" if due else ""


def record(
    db: Session,
    item: InvItem,
    movement_type: MovementTypeEnum,
    actor: Optional[User] = None,
    *,
    to_location_id: Optional[int] = None,
    to_holder_id: Optional[int] = None,
    qty_delta: float = 0.0,
    project_id: Optional[int] = None,
    unit_cost: Optional[float] = None,
    reason: Optional[str] = None,
    reference: Optional[str] = None,
    expected_return_date: Optional[date] = None,
    condition: Optional[str] = None,
    recovery_amount: Optional[float] = None,
    new_status: Optional[str] = None,
    lifecycle_state: Optional[str] = None,
    initial: bool = False,
    commit: bool = True,
) -> InvMovement:
    """
    Write one movement and bring the item into line with it.

    The `from_*` fields are read off the item rather than passed in — the place
    a thing is moving from is a fact the register already knows, and accepting
    it from a caller is an invitation to record a move that never happened.

    `initial` is the one exception. On intake the item has to be constructed
    already sitting somewhere, because the database refuses a row that is
    neither on a shelf nor with a person; without this the very first movement
    would claim the thing moved from the shelf it was just put on.
    """
    movement = InvMovement(
        item_id=item.id,
        movement_type=movement_type.value if hasattr(movement_type, "value") else movement_type,
        from_location_id=None if initial else item.current_location_id,
        to_location_id=to_location_id,
        from_holder_id=None if initial else item.current_holder_id,
        to_holder_id=to_holder_id,
        qty_delta=qty_delta,
        project_id=project_id if project_id is not None else item.project_id,
        unit_cost=unit_cost,
        performed_by=actor.id if actor else None,
        reason=reason,
        reference=reference,
        expected_return_date=expected_return_date,
        condition=condition,
        recovery_amount=recovery_amount,
    )

    item.current_location_id = to_location_id
    item.current_holder_id = to_holder_id
    if new_status is not None:
        item.status = new_status
    if condition is not None:
        item.condition = condition
    if lifecycle_state is not None:
        item.lifecycle_state = lifecycle_state
    if qty_delta:
        item.qty = round(float(item.qty or 0.0) + float(qty_delta), 4)
    if to_holder_id:
        item.hold_due_date = expected_return_date
        # A transfer starts a fresh hold: the clock belongs to whoever is
        # holding it now, not to whoever had it first.
        if to_holder_id != movement.from_holder_id or item.held_since is None:
            item.held_since = datetime.utcnow()
    else:
        item.hold_due_date = None
        item.held_since = None

    # Quarantine is the one location that starts a clock, so the "sitting here
    # over 30 days" report is a column lookup rather than a walk of the log.
    if to_location_id is not None:
        dest = db.query(InvLocation).filter(InvLocation.id == to_location_id).first()
        if dest and dest.location_type == LocationTypeEnum.QUARANTINE.value:
            item.quarantined_at = item.quarantined_at or datetime.utcnow()
        else:
            item.quarantined_at = None
    else:
        item.quarantined_at = None

    assert_placed(item)
    db.add(movement)
    if commit:
        db.commit()
        db.refresh(movement)
    else:
        db.flush()
    return movement


# --- The named movements --------------------------------------------------

def receive(db: Session, item: InvItem, actor: User, *, location_id: int,
            qty: float = 1.0, reference: Optional[str] = None,
            condition: str = ConditionEnum.NEW.value,
            initial: bool = False, commit: bool = True) -> InvMovement:
    """Intake. The single choke point — everything enters the system here."""
    return record(db, item, MovementTypeEnum.RECEIVE, actor,
                  to_location_id=location_id,
                  qty_delta=qty if item.is_quantity_tracked else 0.0,
                  reference=reference, condition=condition,
                  new_status=ItemStatusEnum.IN_STOCK.value,
                  initial=initial, commit=commit)


def checkout(db: Session, item: InvItem, actor: User, holder: User, *,
             expected_return_date: Optional[date] = None,
             condition: Optional[str] = None,
             reason: Optional[str] = None,
             notify: bool = True) -> InvMovement:
    """
    Hand it to somebody. Class A and B only — Class C is issued, not held.

    The expected return date defaults rather than being optional: a hold with
    no date on it never becomes overdue, and a hold that never becomes overdue
    is how a register goes stale without anybody noticing.

    The condition is recorded on the movement, not just on the item. The
    question asked at an exit is not only who had it but what state it was in
    when they got it — otherwise every scratch is arguable, and the argument
    happens on somebody's last day.
    """
    if item.is_quantity_tracked:
        raise _bad(f"{item.tag} is a stock line. Issue a quantity against a "
                   "project rather than checking the bin out.")
    if item.current_holder_id:
        holder_name = item.holder.full_name if item.holder else "someone"
        raise _bad(f"{item.tag} is already with {holder_name}. Record its return first.")
    if item.status in _UNISSUABLE:
        raise _bad(f"{item.tag} is {item.status.replace('_', ' ')} and cannot be issued.")

    due = _due(item, expected_return_date)
    movement = record(db, item, MovementTypeEnum.CHECKOUT, actor,
                      to_holder_id=holder.id, expected_return_date=due,
                      condition=condition or item.condition, reason=reason,
                      new_status=ItemStatusEnum.ISSUED.value)
    if notify:
        _notify(db, holder.id, "Equipment checked out to you",
                f"{item.name} ({item.tag}) is recorded against your name{_until(due)}. "
                "You will be asked to return it when you leave.",
                item.id)
    return movement


def checkin(db: Session, item: InvItem, actor: User, *, location_id: int,
            condition: str = ConditionEnum.WORKING.value,
            recovery_amount: Optional[float] = None,
            reason: Optional[str] = None) -> InvMovement:
    """Back on the shelf. Something faulty goes to repair, not to stock."""
    if not item.current_holder_id:
        raise _bad(f"{item.tag} is not currently with anybody.")
    new_status = (ItemStatusEnum.UNDER_REPAIR.value
                  if condition == ConditionEnum.FAULTY.value
                  else ItemStatusEnum.IN_STOCK.value)
    return record(db, item, MovementTypeEnum.CHECKIN, actor,
                  to_location_id=location_id, condition=condition,
                  recovery_amount=recovery_amount, reason=reason,
                  new_status=new_status)


def transfer(db: Session, item: InvItem, actor: User, to_holder: User, *,
             reason: Optional[str] = None,
             expected_return_date: Optional[date] = None) -> InvMovement:
    """
    Desk to desk, recorded.

    A silent handover is the commonest way custody records go stale — the
    register still names the first person weeks after the board moved, and the
    clearance list at their exit is wrong in both directions.
    """
    if not item.current_holder_id:
        raise _bad(f"{item.tag} is on a shelf, not with anybody. Check it out instead.")
    if item.current_holder_id == to_holder.id:
        raise _bad(f"{item.tag} is already with {to_holder.full_name}.")

    previous_id = item.current_holder_id
    previous = item.holder
    due = _due(item, expected_return_date)
    movement = record(db, item, MovementTypeEnum.TRANSFER, actor,
                      to_holder_id=to_holder.id, expected_return_date=due,
                      condition=item.condition, reason=reason,
                      new_status=ItemStatusEnum.ISSUED.value)
    _notify(db, to_holder.id, "Equipment transferred to you",
            f"{item.name} ({item.tag}) has moved from "
            f"{previous.full_name if previous else 'a colleague'} to you"
            f"{_until(due)}.", item.id)
    _notify(db, previous_id, "Equipment transferred off your name",
            f"{item.name} ({item.tag}) is now recorded against "
            f"{to_holder.full_name}. It is no longer on your clearance list.",
            item.id)
    return movement


def consume(db: Session, item: InvItem, actor: User, *, qty: float,
            project_id: Optional[int], reason: Optional[str] = None) -> InvMovement:
    """
    Class C only: parts used up against a project.

    Every row here carries a project and a unit cost. Summed, that is material
    cost per project — the number that currently gets guessed at quoting time,
    and quietly the highest-value output of the whole module.
    """
    if not item.is_quantity_tracked:
        raise _bad(f"{item.tag} is tracked per unit. Scrap it or check it out; "
                   "it cannot be consumed.")
    if qty <= 0:
        raise _bad("Consumed quantity must be greater than zero.")
    if float(item.qty or 0.0) < qty:
        raise _bad(f"Only {item.qty:g} of {item.tag} on the shelf; {qty:g} requested.")

    return record(db, item, MovementTypeEnum.CONSUME, actor,
                  to_location_id=item.current_location_id,
                  qty_delta=-abs(qty), project_id=project_id,
                  unit_cost=item.effective_unit_cost, reason=reason)


def ship_to_client(db: Session, item: InvItem, actor: User, *, reference: str,
                   expected_return_date: Optional[date] = None,
                   reason: Optional[str] = None) -> InvMovement:
    """
    Out of the building to a client.

    The courier reference is mandatory. An item recorded as shipped with
    nothing to check that against is indistinguishable from an item that was
    lost on the way to the post room.
    """
    if not (reference or "").strip():
        raise _bad("A courier reference is required to ship an item to a client.")
    return record(db, item, MovementTypeEnum.SHIP_TO_CLIENT, actor,
                  reference=reference.strip(),
                  expected_return_date=expected_return_date, reason=reason,
                  new_status=ItemStatusEnum.IN_TRANSIT.value,
                  lifecycle_state=(LifecycleStateEnum.WITH_CLIENT.value
                                   if item.lifecycle_state else None))


def confirm_with_client(db: Session, item: InvItem, actor: User, *,
                        location_id: int,
                        reason: Optional[str] = None) -> InvMovement:
    """Delivery confirmed: in_transit becomes a real place on the client's site."""
    return record(db, item, MovementTypeEnum.SHIP_TO_CLIENT, actor,
                  to_location_id=location_id, reason=reason,
                  reference="delivery confirmed",
                  new_status=ItemStatusEnum.WITH_CLIENT.value)


def return_from_client(db: Session, item: InvItem, actor: User, *,
                       location_id: int,
                       condition: str = ConditionEnum.WORKING.value,
                       reference: Optional[str] = None) -> InvMovement:
    """
    Client hardware coming back, onto its own shelf.

    The caller picks the location and the API refuses anything but a
    client_shelf for client-owned kit — mixing it into our stock is a liability
    problem, not a tidiness one.
    """
    return record(db, item, MovementTypeEnum.RETURN_FROM_CLIENT, actor,
                  to_location_id=location_id, condition=condition,
                  reference=reference, new_status=ItemStatusEnum.IN_STOCK.value)


def repair_out(db: Session, item: InvItem, actor: User, *, reference: str,
               reason: Optional[str] = None) -> InvMovement:
    """Off to a vendor. Items under repair still belong to somebody."""
    if not (reference or "").strip():
        raise _bad("A repair vendor reference is required.")
    return record(db, item, MovementTypeEnum.REPAIR_OUT, actor,
                  reference=reference.strip(), reason=reason,
                  condition=ConditionEnum.FAULTY.value,
                  new_status=ItemStatusEnum.UNDER_REPAIR.value)


def repair_in(db: Session, item: InvItem, actor: User, *, location_id: int,
              condition: str = ConditionEnum.WORKING.value,
              reference: Optional[str] = None) -> InvMovement:
    return record(db, item, MovementTypeEnum.REPAIR_IN, actor,
                  to_location_id=location_id, condition=condition,
                  reference=reference, new_status=ItemStatusEnum.IN_STOCK.value)


def scrap(db: Session, item: InvItem, actor: User, *, reason: str) -> InvMovement:
    """
    End of life. Admin only, and the reason is not optional.

    The tag is not freed: a scrapped item stays in the table as a closed record
    so its number is never issued again and its history stays readable.
    """
    if not (reason or "").strip():
        raise _bad("Scrapping an item requires a reason.")
    return record(db, item, MovementTypeEnum.SCRAP, actor, reason=reason.strip(),
                  condition=ConditionEnum.SCRAPPED.value,
                  new_status=ItemStatusEnum.SCRAPPED.value,
                  lifecycle_state=(LifecycleStateEnum.SCRAPPED.value
                                   if item.lifecycle_state else None))


def mark_lost(db: Session, item: InvItem, actor: User, *, reason: str,
              recovery_amount: Optional[float] = None) -> InvMovement:
    """
    Recorded when a count cannot find it. Stays open until a `found` closes it.

    `recovery_amount` is what gets charged to a leaver when this is a write-off
    at clearance; it is the number the final settlement reads.
    """
    return record(db, item, MovementTypeEnum.LOST, actor, reason=reason,
                  recovery_amount=recovery_amount,
                  new_status=ItemStatusEnum.LOST.value)


def mark_found(db: Session, item: InvItem, actor: User, *, location_id: int,
               reason: Optional[str] = None) -> InvMovement:
    """It turned up. Closes the open `lost` rather than deleting it."""
    if item.status != ItemStatusEnum.LOST.value:
        raise _bad(f"{item.tag} is not recorded as lost.")
    return record(db, item, MovementTypeEnum.FOUND, actor,
                  to_location_id=location_id, reason=reason,
                  new_status=ItemStatusEnum.IN_STOCK.value)


def audit_adjust(db: Session, item: InvItem, actor: User, *, qty_delta: float,
                 reason: str, reference: Optional[str] = None) -> InvMovement:
    """
    Correct a count. The only way the past is ever changed.

    Never an edit: the wrong number and the correction both stay readable,
    which is what makes a drifting category visible as a pattern rather than
    as a one-off somebody already tidied away.
    """
    if not (reason or "").strip():
        raise _bad("An adjustment requires a reason.")
    return record(db, item, MovementTypeEnum.AUDIT_ADJUST, actor,
                  to_location_id=item.current_location_id,
                  to_holder_id=item.current_holder_id,
                  qty_delta=qty_delta, reason=reason.strip(), reference=reference)


def write_off(db: Session, item: InvItem, actor: User, *, reason: str,
              recovery_amount: Optional[float] = None) -> InvMovement:
    """
    Give up on an item a leaver never returned, and charge for it.

    The one way a clearance line closes without the hardware coming back. Admin
    only, reason mandatory, and the amount lands on the final settlement as
    `asset_recovery` — so "written off" is a decision with a number attached
    rather than a checkbox someone ticked to unblock an exit.
    """
    if not (reason or "").strip():
        raise _bad("A write-off requires a reason.")
    return record(db, item, MovementTypeEnum.LOST, actor,
                  reason=f"Written off at clearance: {reason.strip()}",
                  recovery_amount=recovery_amount,
                  new_status=ItemStatusEnum.LOST.value)


# --- Queries the rest of the app asks -------------------------------------

def holdings_for(db: Session, employee_id: int):
    """
    Everything this person is holding right now.

    This is the function the offboarding clearance gate is built on, which is
    why the whole module has one register rather than two: a second one would
    be invisible here, and somebody could walk out with every instrument in the
    lab and still clear their exit cleanly.
    """
    return (db.query(InvItem)
            .filter(InvItem.current_holder_id == employee_id)
            .order_by(InvItem.tag)
            .all())


def recovery_total_for(db: Session, employee_id: int) -> float:
    """
    What has been charged against this person's equipment.

    Only amounts somebody explicitly entered on a return or a write-off —
    nothing is charged automatically for kit that is merely still outstanding.
    Unreturned equipment blocks clearance instead, which is a conversation
    rather than a deduction.
    """
    rows = (db.query(InvMovement)
            .filter(InvMovement.from_holder_id == employee_id,
                    InvMovement.recovery_amount.isnot(None))
            .all())
    return round(sum(float(m.recovery_amount or 0.0) for m in rows), 2)


def _notify(db: Session, user_id: int, title: str, message: str,
            item_id: int) -> None:
    """Best effort: a notification failure must not undo a recorded movement."""
    try:
        create_notification(db, user_id, title, message,
                            NotificationTypeEnum.INFO, related_id=item_id,
                            related_type="inventory_item")
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("Could not notify user %s about item %s", user_id, item_id)
