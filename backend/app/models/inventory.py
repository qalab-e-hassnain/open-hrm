"""
Lab inventory: what we own, where it is, and who is holding it.

This grew out of the old two-table asset register (`assets` +
`asset_assignments`) rather than being built beside it, and the reason is the
offboarding gate. Clearance refuses to let someone leave while equipment is
still issued to them, and that check reads custody out of one place. A second
register sitting next to the first would be invisible to it, so a leaver could
walk out with every oscilloscope in the lab and clear their exit cleanly.

Three classes of thing live in one table, told apart by the category's
`tracking_type`:

  serial   (A) one row per unit, serial recorded, assigned to a person or a
               place at all times — instruments, dev kits, compute modules
  project  (B) one row per unit, belongs to a project, carries a lifecycle
               state — prototype boards, client samples
  quantity (C) counted, not chased — passives, connectors, wire

The movement log is the point of the whole design. `inv_movements` is append
only: never updated, never deleted, by anyone, including an admin. Corrections
are new `audit_adjust` rows. The moment the past can be quietly rewritten the
trail is worth nothing, so this is enforced twice — a trigger in Postgres (see
the migration) and the session guard at the bottom of this file, which is the
half the SQLite test suite can actually see.
"""
from sqlalchemy import (Boolean, Column, Date, DateTime, Float, ForeignKey,
                        Index, Integer, String, Text, event)
from sqlalchemy.orm import Session, relationship
from datetime import datetime
import enum

from app.database import Base


# --- Vocabulary -----------------------------------------------------------
#
# Every state field below is a String column validated by one of these enums
# rather than a native database enum. Two reasons, both learned here: the
# Notification model already does this because native enums fight SQLAlchemy
# over case, and adding a value to a native Postgres enum means ALTER TYPE,
# which cannot be used in the same migration that writes rows carrying the new
# value. Thirteen movement types is a list that will grow; growing it should be
# a code change with no migration at all.

class TrackingTypeEnum(str, enum.Enum):
    SERIAL = "serial"        # Class A
    PROJECT = "project"      # Class B
    QUANTITY = "quantity"    # Class C


class CategoryDomainEnum(str, enum.Enum):
    """
    Which side of the business owns a category.

    One register underneath, so an exit still finds every laptop and every dev
    kit in one query. But the lab custodian runs the lab, and office equipment
    (the old asset register: laptops, phones, SIMs, furniture) stays with admin
    and HR, issued for as long as the job lasts rather than on an expiring hold.
    """
    LAB = "lab"
    OFFICE = "office"


class LocationTypeEnum(str, enum.Enum):
    INTERNAL = "internal"            # our shelf, our hardware
    CLIENT_SHELF = "client_shelf"    # client-owned only, never mixed
    IN_TRANSIT = "in_transit"        # shipped, needs a courier reference
    WITH_PERSON = "with_person"      # virtual: resolves to the employee record
    CUSTOMS_HOLD = "customs_hold"    # imported, awaiting clearance
    QUARANTINE = "quarantine"        # faulty or unidentified, clear within 30 days


class ItemStatusEnum(str, enum.Enum):
    IN_STOCK = "in_stock"
    ISSUED = "issued"
    IN_TRANSIT = "in_transit"
    WITH_CLIENT = "with_client"
    UNDER_REPAIR = "under_repair"
    LOST = "lost"
    SCRAPPED = "scrapped"


class ConditionEnum(str, enum.Enum):
    NEW = "new"
    WORKING = "working"
    FAULTY = "faulty"
    SCRAPPED = "scrapped"
    # Seen on the shelf, never powered on. The physical count proves a thing is
    # there; it does not prove it works, and recording 180 untested modules as
    # "working" would be asserting something nobody checked. The count sheet's
    # own vocabulary has this value, which is the tell that the process needs
    # it. Costs nothing to carry: the column is a String.
    UNKNOWN = "unknown"


class OwnershipEnum(str, enum.Enum):
    COMPANY = "company"
    CLIENT = "client"
    LOANED_IN = "loaned_in"
    LOANED_OUT = "loaned_out"


class LifecycleStateEnum(str, enum.Enum):
    """Class B only: where a prototype board is in its life."""
    IN_BUILD = "in_build"
    UNDER_TEST = "under_test"
    WITH_CLIENT = "with_client"
    SHELVED = "shelved"
    SCRAPPED = "scrapped"


class MovementTypeEnum(str, enum.Enum):
    RECEIVE = "receive"
    CHECKOUT = "checkout"
    CHECKIN = "checkin"
    TRANSFER = "transfer"
    SHIP_TO_CLIENT = "ship_to_client"
    RETURN_FROM_CLIENT = "return_from_client"
    CONSUME = "consume"
    REPAIR_OUT = "repair_out"
    REPAIR_IN = "repair_in"
    SCRAP = "scrap"
    LOST = "lost"
    FOUND = "found"
    AUDIT_ADJUST = "audit_adjust"


class RequestStatusEnum(str, enum.Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    ISSUED = "issued"        # terminal; carries the movement that satisfied it
    CANCELLED = "cancelled"


class AuditSessionStatusEnum(str, enum.Enum):
    OPEN = "open"
    CLOSED = "closed"


class AuditOutcomeEnum(str, enum.Enum):
    PRESENT = "present"
    FOUND_ELSEWHERE = "found_elsewhere"
    WITH_PERSON = "with_person"
    NOT_LOCATED = "not_located"


# --- Tables ---------------------------------------------------------------

class InvCategory(Base):
    """
    What kind of thing this is, and therefore how hard we chase it.

    `tracking_type` is the one field that drives every difference in behaviour
    between a CM5 and a 0402 resistor. Nothing in the service layer branches on
    a hardcoded category list; it all reads this.
    """
    __tablename__ = "inv_categories"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False, unique=True, index=True)
    tracking_type = Column(String, nullable=False,
                           default=TrackingTypeEnum.SERIAL.value)
    # Prefix the tag generator issues against, e.g. "QGT-EQ-". Null means this
    # category does not generate tags and the custodian types one.
    tag_prefix = Column(String, nullable=True)
    serial_required = Column(Boolean, default=False, nullable=False,
                             server_default="false")
    default_audit_days = Column(Integer, default=30, nullable=False)
    domain = Column(String, nullable=False, default=CategoryDomainEnum.LAB.value,
                    server_default=CategoryDomainEnum.LAB.value, index=True)
    is_active = Column(Boolean, default=True, nullable=False, server_default="true")
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    items = relationship("InvItem", back_populates="category")


class InvLocation(Base):
    """
    A shelf, a bin, a client's site. Self-referencing tree.

    A location that is not physically labelled does not exist here — the code
    on the row is the code printed on the shelf, and the count is checked
    against the room by reading one and finding the other.
    """
    __tablename__ = "inv_locations"

    id = Column(Integer, primary_key=True, index=True)
    parent_id = Column(Integer, ForeignKey("inv_locations.id"), nullable=True,
                       index=True)
    code = Column(String, nullable=False, unique=True, index=True)   # L1-R3-S2-B14
    name = Column(String, nullable=False)
    location_type = Column(String, nullable=False,
                           default=LocationTypeEnum.INTERNAL.value, index=True)
    # Set on a client_shelf node so the separation is a fact about the shelf
    # rather than a convention about what gets put on it.
    client_id = Column(Integer, nullable=True, index=True)
    client_name = Column(String, nullable=True)
    is_active = Column(Boolean, default=True, nullable=False, server_default="true")
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    parent = relationship("InvLocation", remote_side=[id], backref="children")

    @property
    def path(self) -> str:
        """Human trail from the root, for a list that has to read at a glance."""
        names, node, seen = [], self, set()
        while node is not None and node.id not in seen:
            seen.add(node.id)
            names.append(node.name)
            node = node.parent
        return " / ".join(reversed(names))


class InvItem(Base):
    """
    One thing, or one bin of things.

    Renamed from `assets` rather than created beside it, so every id already
    quoted in a clearance line, a settlement or an audit row still points at
    the same physical object.
    """
    __tablename__ = "inv_items"

    id = Column(Integer, primary_key=True, index=True)
    # The sticker on the machine. Unique, so two people cannot both claim to
    # hold "the Dell".
    tag = Column(String, nullable=False, unique=True, index=True)
    category_id = Column(Integer, ForeignKey("inv_categories.id"), nullable=False,
                         index=True)

    name = Column(String, nullable=False)
    manufacturer = Column(String, nullable=True)
    mpn = Column(String, nullable=True, index=True)      # manufacturer part number
    description = Column(Text, nullable=True)
    serial_number = Column(String, nullable=True, index=True)

    purchase_date = Column(Date, nullable=True)
    purchase_cost = Column(Float, nullable=True)
    currency = Column(String, nullable=True)
    supplier = Column(String, nullable=True)
    invoice_ref = Column(String, nullable=True)
    # What it would cost to replace today. Kept apart from the purchase price,
    # which stops being the right number the year after.
    replacement_cost = Column(Float, nullable=True)
    # Unit cost used when a Class C line is consumed against a project. Falls
    # back to purchase_cost / qty when it was never set explicitly.
    unit_cost = Column(Float, nullable=True)

    status = Column(String, nullable=False, default=ItemStatusEnum.IN_STOCK.value,
                    index=True)
    condition = Column(String, nullable=False, default=ConditionEnum.WORKING.value)
    ownership = Column(String, nullable=False, default=OwnershipEnum.COMPANY.value,
                       index=True)
    lifecycle_state = Column(String, nullable=True)      # Class B only

    # References into the project system. Deliberately plain integers with no
    # foreign key: projects live in another application, and a cross-app FK
    # would couple the two deploy pipelines for ever. The id is the link; the
    # code and name are a cache so a list renders without a round trip.
    client_id = Column(Integer, nullable=True, index=True)
    client_name = Column(String, nullable=True)
    project_id = Column(Integer, nullable=True, index=True)
    project_code = Column(String, nullable=True)
    project_name = Column(String, nullable=True)

    # Exactly one of these is set while the item is in_stock or issued. A
    # scrapped, lost or in-transit item legitimately has neither. Enforced by a
    # CHECK in Postgres and by the service layer everywhere.
    current_location_id = Column(Integer, ForeignKey("inv_locations.id"),
                                 nullable=True, index=True)
    current_holder_id = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"),
                               nullable=True, index=True)
    # When the current hold falls due. Copied off the checkout movement so the
    # overdue sweep is one indexed query rather than a walk of the log.
    hold_due_date = Column(Date, nullable=True, index=True)
    # When the current holder took it. Same reasoning: "days held" appears on
    # every row of the holdings list, and deriving it from the log would make
    # that list one query per item.
    held_since = Column(DateTime, nullable=True)
    # Set when an item lands in quarantine, so "over 30 days" is answerable.
    quarantined_at = Column(DateTime, nullable=True)

    qty = Column(Float, nullable=False, default=1.0)     # Class A/B always 1
    min_qty = Column(Float, nullable=True)               # Class C reorder point

    photo_url = Column(String, nullable=True)
    datasheet_url = Column(String, nullable=True)
    notes = Column(Text, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)

    category = relationship("InvCategory", back_populates="items")
    location = relationship("InvLocation", foreign_keys=[current_location_id])
    holder = relationship("User", foreign_keys=[current_holder_id])
    movements = relationship("InvMovement", back_populates="item",
                             cascade="all, delete-orphan",
                             order_by="desc(InvMovement.created_at)")

    @property
    def tracking_type(self) -> str:
        return self.category.tracking_type if self.category else TrackingTypeEnum.SERIAL.value

    @property
    def domain(self) -> str:
        return self.category.domain if self.category else CategoryDomainEnum.LAB.value

    @property
    def is_office(self) -> bool:
        return self.domain == CategoryDomainEnum.OFFICE.value

    @property
    def is_quantity_tracked(self) -> bool:
        return self.tracking_type == TrackingTypeEnum.QUANTITY.value

    @property
    def effective_unit_cost(self) -> float:
        """What one of these cost us. Feeds material cost per project."""
        if self.unit_cost is not None:
            return float(self.unit_cost)
        if self.purchase_cost and self.qty:
            return round(float(self.purchase_cost) / float(self.qty), 4)
        return 0.0

    @property
    def variance_unit_value(self) -> float:
        """
        What one of these missing is worth.

        Replacement cost, not purchase cost: a variance report is read to
        decide whether something needs chasing, and the number that answers
        that is what buying another one costs today. Stock lines are the
        exception — a reel of resistors has no meaningful replacement cost per
        unit beyond what it cost, so those fall back to the purchase figure.
        """
        if self.is_quantity_tracked:
            return self.effective_unit_cost
        return float(self.replacement_cost or self.purchase_cost or 0.0)


class InvMovement(Base):
    """
    One thing that happened to one item. Append only.

    Never updated and never deleted, by anyone. A mistake is corrected with a
    new `audit_adjust` row that says what was wrong, which leaves both the
    error and the correction readable a year later — a silent edit leaves
    neither.
    """
    __tablename__ = "inv_movements"

    id = Column(Integer, primary_key=True, index=True)
    item_id = Column(Integer, ForeignKey("inv_items.id"), nullable=False, index=True)
    movement_type = Column(String, nullable=False, index=True)

    from_location_id = Column(Integer, ForeignKey("inv_locations.id"), nullable=True)
    to_location_id = Column(Integer, ForeignKey("inv_locations.id"), nullable=True)
    from_holder_id = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"),
                            nullable=True, index=True)
    to_holder_id = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"),
                          nullable=True, index=True)

    # Negative on a consume, positive on a receive, zero for custody moves that
    # do not change how much of the thing exists.
    qty_delta = Column(Float, nullable=False, default=0.0)
    # Stamped on every consume; summed, this is material cost per project,
    # which is currently a guess and feeds quoting directly.
    project_id = Column(Integer, nullable=True, index=True)
    unit_cost = Column(Float, nullable=True)

    performed_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    reason = Column(Text, nullable=True)
    # Courier tracking number, repair vendor docket, audit session id — whatever
    # outside this system the movement can be checked against.
    reference = Column(String, nullable=True)
    expected_return_date = Column(Date, nullable=True)

    # The item's condition at the moment of this movement. One field rather
    # than the old condition_out/condition_in pair, because that pair only
    # existed to fit an issue and a return onto a single row.
    condition = Column(String, nullable=True)
    # Charged to a leaver on the final settlement when something comes back
    # broken, or does not come back at all.
    recovery_amount = Column(Float, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)

    item = relationship("InvItem", back_populates="movements")
    performer = relationship("User", foreign_keys=[performed_by])
    to_holder = relationship("User", foreign_keys=[to_holder_id])
    from_holder = relationship("User", foreign_keys=[from_holder_id])


class InvRequest(Base):
    """An engineer asks, a lead approves, the custodian issues."""
    __tablename__ = "inv_requests"

    id = Column(Integer, primary_key=True, index=True)
    requester_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    # One or the other: a specific item, or "something from this category".
    item_id = Column(Integer, ForeignKey("inv_items.id"), nullable=True, index=True)
    category_id = Column(Integer, ForeignKey("inv_categories.id"), nullable=True)
    qty = Column(Float, nullable=False, default=1.0)
    project_id = Column(Integer, nullable=True, index=True)
    purpose = Column(Text, nullable=False)
    needed_by = Column(Date, nullable=True)

    status = Column(String, nullable=False, default=RequestStatusEnum.PENDING.value,
                    index=True)
    approver_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    approved_at = Column(DateTime, nullable=True)
    rejection_reason = Column(Text, nullable=True)
    issued_movement_id = Column(Integer, ForeignKey("inv_movements.id"), nullable=True)
    # So the 48-hour approval nudge fires once rather than on every sweep.
    reminded_at = Column(DateTime, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    requester = relationship("User", foreign_keys=[requester_id])
    approver = relationship("User", foreign_keys=[approver_id])
    item = relationship("InvItem", foreign_keys=[item_id])
    category = relationship("InvCategory", foreign_keys=[category_id])


class InvAuditSession(Base):
    """
    One rolling count, scoped to a location or a category.

    Annual full stocktakes get skipped because they hurt; a thirty-minute
    scan of one rack does not.
    """
    __tablename__ = "inv_audit_sessions"

    id = Column(Integer, primary_key=True, index=True)
    scope_location_id = Column(Integer, ForeignKey("inv_locations.id"), nullable=True)
    scope_category_id = Column(Integer, ForeignKey("inv_categories.id"), nullable=True)
    label = Column(String, nullable=True)

    status = Column(String, nullable=False, default=AuditSessionStatusEnum.OPEN.value,
                    index=True)
    opened_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    opened_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    closed_at = Column(DateTime, nullable=True)

    variance_count = Column(Integer, default=0, nullable=False)
    variance_value = Column(Float, default=0.0, nullable=False)
    notes = Column(Text, nullable=True)

    lines = relationship("InvAuditLine", back_populates="session",
                         cascade="all, delete-orphan")
    scope_location = relationship("InvLocation", foreign_keys=[scope_location_id])
    scope_category = relationship("InvCategory", foreign_keys=[scope_category_id])
    opener = relationship("User", foreign_keys=[opened_by])


class InvAuditLine(Base):
    """One item the session expected to find, and what was actually found."""
    __tablename__ = "inv_audit_lines"

    id = Column(Integer, primary_key=True, index=True)
    session_id = Column(Integer, ForeignKey("inv_audit_sessions.id"), nullable=False,
                        index=True)
    item_id = Column(Integer, ForeignKey("inv_items.id"), nullable=False, index=True)

    expected_qty = Column(Float, nullable=False, default=0.0)
    counted_qty = Column(Float, nullable=True)
    variance = Column(Float, nullable=False, default=0.0)
    variance_value = Column(Float, nullable=False, default=0.0)
    outcome = Column(String, nullable=True)
    note = Column(Text, nullable=True)
    scanned_at = Column(DateTime, nullable=True)

    session = relationship("InvAuditSession", back_populates="lines")
    item = relationship("InvItem", foreign_keys=[item_id])


class InvTagCounter(Base):
    """
    Next number per tag prefix.

    A MAX(tag)+1 scan cannot promise what the design asks for — a number that
    is never reused, including after a scrap — because a scrapped tag stays in
    the table and a deleted one would hand its number back. A counter only ever
    goes up.
    """
    __tablename__ = "inv_tag_counters"

    prefix = Column(String, primary_key=True)
    next_number = Column(Integer, nullable=False, default=1)


# The two questions actually asked of the log: what is this person holding, and
# what has happened to this item.
Index("ix_inv_movements_item_time", InvMovement.item_id, InvMovement.created_at)
Index("ix_inv_movements_project_type", InvMovement.project_id,
      InvMovement.movement_type)
# Overdue holds: one indexed scan rather than a walk of every open checkout.
Index("ix_inv_items_holder_due", InvItem.current_holder_id, InvItem.hold_due_date)


# --- The append-only guard ------------------------------------------------

class AppendOnlyViolation(Exception):
    """Something tried to rewrite history. It does not get to."""


@event.listens_for(Session, "before_flush")
def _movements_are_append_only(session, flush_context, instances):
    """
    Refuse any UPDATE or DELETE of a movement row, from anywhere in the app.

    Postgres has a trigger that does the same thing and is the real guarantee —
    it holds against psql and against code nobody has written yet. This exists
    because the test suite builds its schema with `Base.metadata.create_all`
    against SQLite and therefore never runs that trigger, so without this the
    single most important invariant in the module would be untested.
    """
    for obj in session.dirty:
        if isinstance(obj, InvMovement) and session.is_modified(
                obj, include_collections=False):
            raise AppendOnlyViolation(
                f"Movement {obj.id} cannot be edited. Record an audit_adjust "
                "movement instead.")
    for obj in session.deleted:
        if isinstance(obj, InvMovement):
            raise AppendOnlyViolation(
                f"Movement {obj.id} cannot be deleted. History is append only.")
