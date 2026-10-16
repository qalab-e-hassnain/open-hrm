"""
The lab inventory API.

Read access is wide and write access is narrow, which is the design's shape:
everybody can see the catalogue and check something out to themselves, because
a system people have to queue for is a system people walk around. What costs
money — receiving, issuing stock, editing records, writing something off —
belongs to the custodian and to admin.

Every custody change delegates to app/utils/inv_movements.py. Nothing here
writes a movement row directly.
"""
from datetime import date, datetime, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from app.api.deps import (can_approve_request_for, get_current_user,
                          is_custodian, is_privileged, require_admin_or_hr,
                          require_custodian)
from app.config import settings
from app.database import get_db
from app.models.inventory import (AuditSessionStatusEnum, CategoryDomainEnum,
                                  ConditionEnum,
                                  InvAuditLine, InvAuditSession, InvCategory,
                                  InvItem, InvLocation, InvMovement,
                                  InvRequest, ItemStatusEnum,
                                  LocationTypeEnum, MovementTypeEnum,
                                  OwnershipEnum, RequestStatusEnum,
                                  TrackingTypeEnum)
from app.models.notification import NotificationTypeEnum
from app.models.user import RoleEnum, User
from app.schemas.inventory import (AdjustIn, AuditCloseIn, AuditLineOut,
                                   AuditScanIn, AuditSessionIn,
                                   AuditSessionOut, CategoryIn, CategoryOut,
                                   CategoryUpdate, CheckinIn, CheckoutIn,
                                   ConfirmDeliveryIn, ConsumeIn, FoundIn,
                                   HolderRow, ItemIn, ItemOut, ItemUpdate,
                                   LocationIn, LocationOut, LocationUpdate,
                                   LostIn, MovementOut, ProjectCostRow,
                                   ReasonIn, ReceiveIn, RenewIn, RepairInIn,
                                   RepairOutIn, RequestDecisionIn, RequestIn,
                                   RequestIssueIn, RequestOut,
                                   ReturnFromClientIn, ScanOut, ShipToClientIn,
                                   StockRow, TransferIn, WriteOffIn)
from app.utils import audit, inv_audit, inv_movements, inv_tags
from app.utils.notifications import create_notification

router = APIRouter()

COST_FIELDS = ("purchase_cost", "replacement_cost", "unit_cost", "supplier",
               "invoice_ref", "purchase_date")


# --- Shared helpers -------------------------------------------------------

def _bad(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail)


def _missing(what: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=what)


def _item_out(item: InvItem, viewer: User, db: Session = None) -> ItemOut:
    out = ItemOut.model_validate(item, from_attributes=True)
    out.category_name = item.category.name if item.category else None
    out.domain = item.domain
    out.tracking_type = item.tracking_type
    if item.location:
        out.location_code = item.location.code
        out.location_path = item.location.path
    if item.holder:
        out.holder_name = item.holder.full_name
    if item.held_since:
        out.days_held = (datetime.utcnow() - item.held_since).days
    out.is_overdue = bool(item.hold_due_date and item.hold_due_date < date.today())
    # The catalogue is open to everybody; what things cost is not.
    if not (is_privileged(viewer, db) or bool(getattr(viewer, "is_lab_custodian", False))):
        for field in COST_FIELDS:
            setattr(out, field, None)
    return out


def _movement_out(m: InvMovement, codes: dict = None) -> MovementOut:
    out = MovementOut.model_validate(m, from_attributes=True)
    if m.item:
        out.item_tag, out.item_name = m.item.tag, m.item.name
    out.from_holder_name = m.from_holder.full_name if m.from_holder else None
    out.to_holder_name = m.to_holder.full_name if m.to_holder else None
    out.performed_by_name = m.performer.full_name if m.performer else None
    # Codes rather than ids, because a timeline that says "moved to 7" is not
    # a timeline anybody reads. Passed in as a lookup so rendering a history
    # is one query for the shelves rather than one per row.
    if codes is not None:
        out.from_location_code = codes.get(m.from_location_id)
        out.to_location_code = codes.get(m.to_location_id)
    return out


def _location_codes(db: Session) -> dict:
    return {i: c for i, c in db.query(InvLocation.id, InvLocation.code).all()}


def _load_item(db: Session, item_id: int) -> InvItem:
    item = (db.query(InvItem)
            .options(joinedload(InvItem.category), joinedload(InvItem.location),
                     joinedload(InvItem.holder))
            .filter(InvItem.id == item_id).first())
    if not item:
        raise _missing("Item not found")
    return item


def _manages(domain: str, user: User, db: Session) -> bool:
    """
    Custodian reach over one side of the register.

    The lab custodian runs the lab. Office equipment is what the asset register
    always was — admin and HR issue it and take it back — so the flag stops at
    the lab door, and admin-level reach covers both.
    """
    if is_privileged(user, db):
        return True
    return (domain != CategoryDomainEnum.OFFICE.value
            and bool(getattr(user, "is_lab_custodian", False)))


def _assert_manages(domain: str, user: User, db: Session) -> None:
    if not _manages(domain, user, db):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Office equipment is issued and recorded by admin or HR.")


def _domain_filter(q, domain: Optional[str]):
    """Narrow an InvItem query to one side of the register."""
    if not domain:
        return q
    if domain not in {d.value for d in CategoryDomainEnum}:
        raise _bad(f"Unknown section '{domain}'. Use 'lab' or 'office'.")
    return q.join(InvCategory, InvItem.category_id == InvCategory.id).filter(
        InvCategory.domain == domain)


def _location_subtree_ids(db: Session, location_id: int) -> set:
    """
    A location and everything inside it.

    Locations are a tree — a building holds rooms, a room holds shelves — but
    an item is stored against the exact node it sits on, which is the shelf.
    Filtering on equality therefore answered "which items are sitting on the
    building itself", which is none of them, and picking a site from the
    filter returned nothing while every item on the page was inside it.

    Read in one query and walked here. The tree is a few dozen rows and this
    runs on a list endpoint, so a recursive CTE would cost a round trip to a
    database on another continent to save nothing.
    """
    rows = db.query(InvLocation.id, InvLocation.parent_id).all()
    children: dict = {}
    for child_id, parent_id in rows:
        children.setdefault(parent_id, []).append(child_id)

    found = set()
    stack = [location_id]
    while stack:
        node = stack.pop()
        if node in found:            # a cycle would otherwise never end
            continue
        found.add(node)
        stack.extend(children.get(node, ()))
    return found


def _load_location(db: Session, location_id: int) -> InvLocation:
    loc = db.query(InvLocation).filter(InvLocation.id == location_id).first()
    if not loc:
        raise _missing("Location not found")
    if not loc.is_active:
        raise _bad(f"Location {loc.code} is no longer in use.")
    return loc


def _load_employee(db: Session, employee_id: int) -> User:
    user = db.query(User).filter(User.id == employee_id).first()
    if not user:
        raise _missing("Employee not found")
    if not user.is_active:
        raise _bad(f"{user.full_name} is no longer active.")
    return user


def _assert_shelf_matches_ownership(item: InvItem, location: InvLocation) -> None:
    """
    Client hardware goes on the client shelf, and nothing else does.

    This is a liability matter rather than a tidiness one: client-owned kit
    mixed into our stock is hard to defend if it is ever disputed, and the
    place to stop the mixing is the moment something is put down.
    """
    is_client_owned = item.ownership == OwnershipEnum.CLIENT.value
    is_client_shelf = location.location_type == LocationTypeEnum.CLIENT_SHELF.value
    if is_client_owned and not is_client_shelf:
        raise _bad(f"{item.tag} is client-owned and belongs on a client shelf, "
                   f"not on {location.code}.")
    if is_client_shelf and not is_client_owned:
        raise _bad(f"{location.code} is a client shelf. {item.tag} is "
                   f"{item.ownership.replace('_', ' ')} and cannot go there.")


def _allowed_actions(item: InvItem, viewer: User, db: Session) -> List[str]:
    """What this caller may do with this item, right now."""
    custodian, privileged = _manages(item.domain, viewer, db), is_privileged(viewer, db)
    actions: List[str] = []

    if item.status in (ItemStatusEnum.SCRAPPED.value,):
        return actions
    if item.status == ItemStatusEnum.LOST.value:
        return ["found"] if custodian else []

    if item.is_quantity_tracked:
        if custodian:
            actions += ["consume", "adjust"]
        return actions

    if item.is_office:
        # Issued and taken back by admin or HR, never self-served, and held
        # for the length of the job rather than on a hold that expires.
        if custodian:
            if item.current_holder_id:
                actions += ["checkin", "transfer"]
            elif item.status == ItemStatusEnum.IN_STOCK.value:
                actions.append("checkout")
            actions.append("repair_out" if item.status != ItemStatusEnum.UNDER_REPAIR.value
                           else "repair_in")
        if privileged:
            actions += ["scrap", "lost"]
        return sorted(set(actions))

    if item.current_holder_id == viewer.id:
        actions += ["checkin", "transfer", "renew"]
    elif item.current_holder_id:
        if custodian:
            actions += ["checkin", "transfer"]
    elif item.status == ItemStatusEnum.IN_STOCK.value:
        actions.append("checkout")

    if custodian:
        actions += ["repair_out" if item.status != ItemStatusEnum.UNDER_REPAIR.value
                    else "repair_in"]
        if item.status == ItemStatusEnum.IN_TRANSIT.value:
            actions.append("confirm_delivery")
        if item.status == ItemStatusEnum.WITH_CLIENT.value:
            actions.append("return_from_client")
        elif item.status == ItemStatusEnum.IN_STOCK.value:
            actions.append("ship_to_client")
    if privileged:
        actions += ["scrap", "lost"]
    return sorted(set(actions))


# --- Categories -----------------------------------------------------------

@router.get("/categories", response_model=List[CategoryOut])
def list_categories(include_inactive: bool = False, domain: Optional[str] = None,
                    db: Session = Depends(get_db),
                    current_user: User = Depends(get_current_user)):
    q = db.query(InvCategory)
    if domain:
        q = q.filter(InvCategory.domain == domain)
    if not include_inactive:
        q = q.filter(InvCategory.is_active == True)            # noqa: E712
    counts = dict(db.query(InvItem.category_id, func.count(InvItem.id))
                  .group_by(InvItem.category_id).all())
    out = []
    for c in q.order_by(InvCategory.name).all():
        row = CategoryOut.model_validate(c, from_attributes=True)
        row.item_count = counts.get(c.id, 0)
        out.append(row)
    return out


@router.post("/categories", response_model=CategoryOut,
             status_code=status.HTTP_201_CREATED)
def create_category(payload: CategoryIn, request: Request,
                    db: Session = Depends(get_db),
                    current_user: User = Depends(require_custodian)):
    _assert_manages(payload.domain.value, current_user, db)
    if db.query(InvCategory).filter(InvCategory.name == payload.name).first():
        raise _bad(f"A category called '{payload.name}' already exists.")
    data = payload.model_dump()
    data["tracking_type"] = payload.tracking_type.value
    data["domain"] = payload.domain.value
    category = InvCategory(**data)
    db.add(category)
    db.commit()
    db.refresh(category)
    audit.record(db, "inventory.category_created", actor=current_user,
                 request=request, target_type="inv_category",
                 target_id=category.id, detail={"name": category.name})
    return CategoryOut.model_validate(category, from_attributes=True)


@router.put("/categories/{category_id}", response_model=CategoryOut)
def update_category(category_id: int, payload: CategoryUpdate, request: Request,
                    db: Session = Depends(get_db),
                    current_user: User = Depends(require_custodian)):
    category = db.query(InvCategory).filter(InvCategory.id == category_id).first()
    if not category:
        raise _missing("Category not found")
    _assert_manages(category.domain, current_user, db)
    changes = payload.model_dump(exclude_unset=True)
    if changes.get("domain") is not None:
        changes["domain"] = changes["domain"].value
        _assert_manages(changes["domain"], current_user, db)
    if "tracking_type" in changes and changes["tracking_type"] is not None:
        changes["tracking_type"] = changes["tracking_type"].value
        if changes["tracking_type"] != category.tracking_type and category.items:
            raise _bad("This category already has items. Changing how they are "
                       "tracked would silently rewrite what every count means.")
    for field, value in changes.items():
        setattr(category, field, value)
    db.commit()
    db.refresh(category)
    audit.record(db, "inventory.category_updated", actor=current_user,
                 request=request, target_type="inv_category",
                 target_id=category.id, detail=changes)
    return CategoryOut.model_validate(category, from_attributes=True)


# --- Locations ------------------------------------------------------------

@router.get("/locations", response_model=List[LocationOut])
def list_locations(include_inactive: bool = False,
                   location_type: Optional[str] = None,
                   db: Session = Depends(get_db),
                   current_user: User = Depends(get_current_user)):
    q = db.query(InvLocation)
    if not include_inactive:
        q = q.filter(InvLocation.is_active == True)             # noqa: E712
    if location_type:
        q = q.filter(InvLocation.location_type == location_type)
    counts = dict(db.query(InvItem.current_location_id, func.count(InvItem.id))
                  .group_by(InvItem.current_location_id).all())
    out = []
    for loc in q.order_by(InvLocation.code).all():
        row = LocationOut.model_validate(loc, from_attributes=True)
        row.path = loc.path
        row.item_count = counts.get(loc.id, 0)
        out.append(row)
    return out


@router.post("/locations", response_model=LocationOut,
             status_code=status.HTTP_201_CREATED)
def create_location(payload: LocationIn, request: Request,
                    db: Session = Depends(get_db),
                    current_user: User = Depends(require_custodian)):
    if db.query(InvLocation).filter(InvLocation.code == payload.code).first():
        raise _bad(f"Location code '{payload.code}' is already in use.")
    if payload.parent_id:
        _load_location(db, payload.parent_id)
    data = payload.model_dump()
    data["location_type"] = payload.location_type.value
    loc = InvLocation(**data)
    db.add(loc)
    db.commit()
    db.refresh(loc)
    audit.record(db, "inventory.location_created", actor=current_user,
                 request=request, target_type="inv_location", target_id=loc.id,
                 detail={"code": loc.code})
    out = LocationOut.model_validate(loc, from_attributes=True)
    out.path = loc.path
    return out


@router.put("/locations/{location_id}", response_model=LocationOut)
def update_location(location_id: int, payload: LocationUpdate, request: Request,
                    db: Session = Depends(get_db),
                    current_user: User = Depends(require_custodian)):
    loc = db.query(InvLocation).filter(InvLocation.id == location_id).first()
    if not loc:
        raise _missing("Location not found")
    changes = payload.model_dump(exclude_unset=True)
    if changes.get("parent_id") == loc.id:
        raise _bad("A location cannot be its own parent.")
    if "location_type" in changes and changes["location_type"] is not None:
        changes["location_type"] = changes["location_type"].value
    if changes.get("is_active") is False:
        held = db.query(InvItem).filter(InvItem.current_location_id == loc.id).count()
        if held:
            raise _bad(f"{held} item(s) are still recorded at {loc.code}. "
                       "Move them before retiring it.")
    for field, value in changes.items():
        setattr(loc, field, value)
    db.commit()
    db.refresh(loc)
    audit.record(db, "inventory.location_updated", actor=current_user,
                 request=request, target_type="inv_location", target_id=loc.id,
                 detail=changes)
    out = LocationOut.model_validate(loc, from_attributes=True)
    out.path = loc.path
    return out


# --- Items ----------------------------------------------------------------

@router.get("/items", response_model=List[ItemOut])
def list_items(
    status_filter: Optional[str] = Query(None, alias="status"),
    category_id: Optional[int] = None,
    location_id: Optional[int] = None,
    held_by: Optional[int] = None,
    project_id: Optional[int] = None,
    ownership: Optional[str] = None,
    overdue_only: bool = False,
    search: Optional[str] = Query(None, description="Matches tag, name, serial or MPN"),
    domain: Optional[str] = Query(None, description="'lab' or 'office'"),
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """The catalogue. Open to everybody; costs are redacted for most people."""
    q = (db.query(InvItem)
         .options(joinedload(InvItem.category), joinedload(InvItem.location),
                  joinedload(InvItem.holder)))
    q = _domain_filter(q, domain)
    if status_filter:
        q = q.filter(InvItem.status == status_filter)
    if category_id:
        q = q.filter(InvItem.category_id == category_id)
    if location_id:
        # Everything inside the chosen location, not only what sits on it.
        q = q.filter(InvItem.current_location_id.in_(
            _location_subtree_ids(db, location_id)))
    if held_by is not None:
        q = q.filter(InvItem.current_holder_id == held_by)
    if project_id is not None:
        q = q.filter(InvItem.project_id == project_id)
    if ownership:
        q = q.filter(InvItem.ownership == ownership)
    if overdue_only:
        q = q.filter(InvItem.hold_due_date.isnot(None),
                     InvItem.hold_due_date < date.today())
    if search:
        like = f"%{search.strip()}%"
        q = q.filter(InvItem.tag.ilike(like) | InvItem.name.ilike(like)
                     | InvItem.serial_number.ilike(like) | InvItem.mpn.ilike(like))
    rows = q.order_by(InvItem.tag).offset(offset).limit(limit).all()
    return [_item_out(i, current_user, db) for i in rows]


@router.get("/items/by-tag/{tag}", response_model=ScanOut)
def scan_item(tag: str, db: Session = Depends(get_db),
              current_user: User = Depends(get_current_user)):
    """
    The scan endpoint. One round trip for the whole screen.

    Section 5 is blunt about why this matters: if scanning takes five seconds
    people scan, and if logging takes thirty seconds of typing people walk off
    with the board instead and the system is dead inside a month. So the item,
    its holder, its location and the actions this caller may take all come back
    together — the screen never has to ask a second question.
    """
    item = (db.query(InvItem)
            .options(joinedload(InvItem.category), joinedload(InvItem.location),
                     joinedload(InvItem.holder))
            .filter(InvItem.tag == tag.strip()).first())
    if not item:
        raise _missing(f"No item carries the tag '{tag}'")
    return ScanOut(
        item=_item_out(item, current_user, db),
        actions=_allowed_actions(item, current_user, db),
        held_by_me=item.current_holder_id == current_user.id,
    )


@router.get("/items/{item_id}", response_model=ItemOut)
def get_item(item_id: int, db: Session = Depends(get_db),
             current_user: User = Depends(get_current_user)):
    return _item_out(_load_item(db, item_id), current_user, db)


@router.post("/items", response_model=ItemOut, status_code=status.HTTP_201_CREATED)
def create_item(payload: ItemIn, request: Request, db: Session = Depends(get_db),
                current_user: User = Depends(require_custodian)):
    """
    Register something, and label it now.

    Intake is the single choke point in the design, and the reason labels are
    printed at the receiving table rather than later is that later never comes.
    """
    category = db.query(InvCategory).filter(
        InvCategory.id == payload.category_id).first()
    if not category:
        raise _missing("Category not found")
    _assert_manages(category.domain, current_user, db)

    data = payload.model_dump()
    data["condition"] = payload.condition.value
    data["ownership"] = payload.ownership.value
    data["lifecycle_state"] = (payload.lifecycle_state.value
                               if payload.lifecycle_state else None)

    tag = (data.pop("tag", None) or "").strip()
    if tag:
        if not inv_tags.is_available(db, tag):
            raise _bad(f"Tag '{tag}' is already in use.")
    else:
        tag = inv_tags.generate(db, category, payload.project_code)

    if category.serial_required and not payload.serial_number:
        raise _bad(f"{category.name} requires a serial number.")
    if (category.tracking_type != TrackingTypeEnum.QUANTITY.value
            and payload.qty not in (1, 1.0)):
        raise _bad("Only stock lines carry a quantity; everything else is one "
                   "unit. Two of a serial-tracked thing is two rows, because "
                   "they are two things.")

    location_id = data.pop("current_location_id", None)
    if not location_id:
        raise _bad("Say where this is being put. Everything in the register is "
                   "on a shelf or with a person from the moment it is "
                   "registered — an inbound item goes to the in-transit node, "
                   "and a licence to its own.")
    location = _load_location(db, location_id)

    # Constructed already placed, because the database refuses a row that is
    # neither on a shelf nor with a person. The intake movement is then written
    # with `initial`, so it does not claim the thing moved from the shelf it
    # was just put on.
    #
    # A stock line starts at zero and the intake movement brings it up to the
    # counted quantity. That way the log accounts for every unit on the shelf
    # rather than for every unit except the first delivery, which is the one
    # gap that would make "where did these come from" unanswerable.
    is_stock = category.tracking_type == TrackingTypeEnum.QUANTITY.value
    intake_qty = float(data.pop("qty", 1.0) or 0.0)
    item = InvItem(**data, tag=tag, created_by=current_user.id,
                   current_location_id=location.id,
                   qty=0.0 if is_stock else 1.0,
                   status=ItemStatusEnum.IN_STOCK.value)
    _assert_shelf_matches_ownership(item, location)
    db.add(item)
    db.flush()

    inv_movements.receive(db, item, current_user, location_id=location.id,
                          qty=intake_qty if is_stock else 0.0,
                          condition=item.condition, reference=payload.invoice_ref,
                          initial=True)
    db.refresh(item)

    audit.record(db, "inventory.item_created", actor=current_user, request=request,
                 target_type="inv_item", target_id=item.id,
                 detail={"tag": item.tag, "name": item.name})
    return _item_out(item, current_user, db)


@router.put("/items/{item_id}", response_model=ItemOut)
def update_item(item_id: int, payload: ItemUpdate, request: Request,
                db: Session = Depends(get_db),
                current_user: User = Depends(require_custodian)):
    """
    Edit the record, not the state.

    Status and location are not in the update schema at all: they are what the
    movement log says they are. Letting them be set here is how a register
    starts disagreeing with its own history.
    """
    item = _load_item(db, item_id)
    _assert_manages(item.domain, current_user, db)
    changes = payload.model_dump(exclude_unset=True)

    if "tag" in changes and changes["tag"] != item.tag:
        if not inv_tags.is_available(db, changes["tag"]):
            raise _bad(f"Tag '{changes['tag']}' is already in use.")
    for enum_field in ("ownership", "lifecycle_state"):
        if changes.get(enum_field) is not None:
            changes[enum_field] = changes[enum_field].value
    if changes.get("ownership") == OwnershipEnum.CLIENT.value and item.location:
        if item.location.location_type != LocationTypeEnum.CLIENT_SHELF.value:
            raise _bad(f"{item.tag} sits on {item.location.code}. Move it to a "
                       "client shelf before marking it client-owned.")

    for field, value in changes.items():
        setattr(item, field, value)
    db.commit()
    db.refresh(item)
    audit.record(db, "inventory.item_updated", actor=current_user, request=request,
                 target_type="inv_item", target_id=item.id, detail=changes)
    return _item_out(item, current_user, db)


# There is deliberately no DELETE. Registering something writes an intake
# movement, and movements are append-only, so nothing in the register is ever
# without history. A mistyped record is corrected with PUT; a thing that should
# not be on the books is scrapped, which keeps the tag and the trail. An
# endpoint that could never succeed is worse than no endpoint.


@router.get("/items/{item_id}/movements", response_model=List[MovementOut])
def item_history(item_id: int, db: Session = Depends(get_db),
                 current_user: User = Depends(get_current_user)):
    """Everything that ever happened to this item. Read only, for everybody."""
    _load_item(db, item_id)
    rows = (db.query(InvMovement)
            .options(joinedload(InvMovement.from_holder),
                     joinedload(InvMovement.to_holder),
                     joinedload(InvMovement.performer))
            .filter(InvMovement.item_id == item_id)
            .order_by(InvMovement.created_at.desc(), InvMovement.id.desc())
            .all())
    codes = _location_codes(db)
    return [_movement_out(m, codes) for m in rows]


# --- Custody --------------------------------------------------------------

@router.post("/items/{item_id}/receive", response_model=ItemOut)
def receive_item(item_id: int, payload: ReceiveIn, request: Request,
                 db: Session = Depends(get_db),
                 current_user: User = Depends(require_custodian)):
    item = _load_item(db, item_id)
    _assert_manages(item.domain, current_user, db)
    location = _load_location(db, payload.location_id)
    _assert_shelf_matches_ownership(item, location)
    inv_movements.receive(db, item, current_user, location_id=location.id,
                          qty=payload.qty if item.is_quantity_tracked else 0.0,
                          reference=payload.reference,
                          condition=payload.condition.value)
    audit.record(db, "inventory.received", actor=current_user, request=request,
                 target_type="inv_item", target_id=item.id,
                 detail={"tag": item.tag, "location": location.code,
                         "qty": payload.qty})
    return _item_out(item, current_user, db)


@router.post("/items/{item_id}/checkout", response_model=ItemOut)
def checkout_item(item_id: int, payload: CheckoutIn, request: Request,
                  db: Session = Depends(get_db),
                  current_user: User = Depends(get_current_user)):
    """
    Take it. Self-service by default — that is the whole point of the scan
    screen — but checking something out to somebody else is a custodian action,
    because otherwise anybody can put anything on anybody's clearance list.
    """
    item = _load_item(db, item_id)
    target_id = payload.employee_id or current_user.id
    if item.is_office:
        _assert_manages(item.domain, current_user, db)
    elif target_id != current_user.id and not _manages(item.domain, current_user, db):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only a custodian can check equipment out to somebody else.")
    holder = _load_employee(db, target_id)
    inv_movements.checkout(db, item, current_user, holder,
                           expected_return_date=payload.expected_return_date,
                           condition=payload.condition.value if payload.condition else None,
                           reason=payload.reason,
                           notify=holder.id != current_user.id)
    audit.record(db, "inventory.checked_out", actor=current_user, request=request,
                 target_type="inv_item", target_id=item.id,
                 detail={"tag": item.tag, "holder_id": holder.id,
                         "due": str(item.hold_due_date or "")})
    return _item_out(item, current_user, db)


@router.post("/items/{item_id}/checkin", response_model=ItemOut)
def checkin_item(item_id: int, payload: CheckinIn, request: Request,
                 db: Session = Depends(get_db),
                 current_user: User = Depends(get_current_user)):
    item = _load_item(db, item_id)
    # Office equipment comes back through admin or HR, not self-service.
    self_service = item.current_holder_id == current_user.id and not item.is_office
    if not self_service and not _manages(item.domain, current_user, db):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the holder or a custodian can record this return.")
    if payload.recovery_amount is not None and not is_privileged(current_user, db):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only admin or HR can put a recovery charge on a return.")
    location = _load_location(db, payload.location_id)
    _assert_shelf_matches_ownership(item, location)
    previous_holder = item.current_holder_id
    inv_movements.checkin(db, item, current_user, location_id=location.id,
                          condition=payload.condition.value,
                          recovery_amount=payload.recovery_amount,
                          reason=payload.reason)
    audit.record(db, "inventory.checked_in", actor=current_user, request=request,
                 target_type="inv_item", target_id=item.id,
                 detail={"tag": item.tag, "from_holder_id": previous_holder,
                         "condition": payload.condition.value,
                         "recovery_amount": payload.recovery_amount})
    return _item_out(item, current_user, db)


@router.post("/items/{item_id}/transfer", response_model=ItemOut)
def transfer_item(item_id: int, payload: TransferIn, request: Request,
                  db: Session = Depends(get_db),
                  current_user: User = Depends(get_current_user)):
    """
    Hand it to a colleague, on the record.

    Either the holder does this or the custodian does. A silent desk-to-desk
    handover is the commonest way a custody record goes stale, and it goes
    stale in both directions at once — the wrong person is chased at their exit
    and the right one is not.
    """
    item = _load_item(db, item_id)
    # Office equipment comes back through admin or HR, not self-service.
    self_service = item.current_holder_id == current_user.id and not item.is_office
    if not self_service and not _manages(item.domain, current_user, db):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the current holder or a custodian can transfer this.")
    to_holder = _load_employee(db, payload.to_employee_id)
    from_id = item.current_holder_id
    inv_movements.transfer(db, item, current_user, to_holder,
                           reason=payload.reason,
                           expected_return_date=payload.expected_return_date)
    audit.record(db, "inventory.transferred", actor=current_user, request=request,
                 target_type="inv_item", target_id=item.id,
                 detail={"tag": item.tag, "from_holder_id": from_id,
                         "to_holder_id": to_holder.id})
    return _item_out(item, current_user, db)


@router.post("/items/{item_id}/renew", response_model=ItemOut)
def renew_hold(item_id: int, payload: RenewIn, db: Session = Depends(get_db),
               current_user: User = Depends(get_current_user)):
    """
    Keep it a while longer.

    One tap, but it is a tap: the point of an expiring hold is not the
    paperwork, it is that somebody looks at the bench and confirms the thing is
    still there before saying so.
    """
    item = _load_item(db, item_id)
    if item.is_office:
        raise _bad(f"{item.tag} is office equipment. It is held for the length "
                   "of the job, so there is no hold to renew.")
    # Office equipment comes back through admin or HR, not self-service.
    self_service = item.current_holder_id == current_user.id and not item.is_office
    if not self_service and not _manages(item.domain, current_user, db):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="Only the holder or a custodian can renew this hold.")
    if not item.current_holder_id:
        raise _bad(f"{item.tag} is not currently held by anybody.")
    days = payload.days or inv_movements.hold_days_for(item)
    item.hold_due_date = date.today() + timedelta(days=days)
    db.commit()
    db.refresh(item)
    return _item_out(item, current_user, db)


@router.post("/items/{item_id}/consume", response_model=ItemOut)
def consume_stock(item_id: int, payload: ConsumeIn, request: Request,
                  db: Session = Depends(get_db),
                  current_user: User = Depends(require_custodian)):
    """
    Parts used up against a project.

    This is the endpoint that produces material cost per project — the figure
    that currently gets guessed at quoting time.
    """
    item = _load_item(db, item_id)
    _assert_manages(item.domain, current_user, db)
    inv_movements.consume(db, item, current_user, qty=payload.qty,
                          project_id=payload.project_id, reason=payload.reason)
    audit.record(db, "inventory.consumed", actor=current_user, request=request,
                 target_type="inv_item", target_id=item.id,
                 detail={"tag": item.tag, "qty": payload.qty,
                         "project_id": payload.project_id})
    return _item_out(item, current_user, db)


@router.post("/items/{item_id}/ship-to-client", response_model=ItemOut)
def ship_to_client(item_id: int, payload: ShipToClientIn, request: Request,
                   db: Session = Depends(get_db),
                   current_user: User = Depends(get_current_user)):
    """A lead may ship with a reference; a custodian or admin always may."""
    if not (is_custodian(current_user, db) or current_user.role == RoleEnum.MANAGER):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="A team lead, custodian or admin ships to a client.")
    item = _load_item(db, item_id)
    if item.is_office:
        _assert_manages(item.domain, current_user, db)
    inv_movements.ship_to_client(db, item, current_user, reference=payload.reference,
                                 expected_return_date=payload.expected_return_date,
                                 reason=payload.reason)
    audit.record(db, "inventory.shipped_to_client", actor=current_user,
                 request=request, target_type="inv_item", target_id=item.id,
                 detail={"tag": item.tag, "reference": payload.reference})
    return _item_out(item, current_user, db)


@router.post("/items/{item_id}/confirm-delivery", response_model=ItemOut)
def confirm_delivery(item_id: int, payload: ConfirmDeliveryIn,
                     db: Session = Depends(get_db),
                     current_user: User = Depends(require_custodian)):
    item = _load_item(db, item_id)
    _assert_manages(item.domain, current_user, db)
    location = _load_location(db, payload.location_id)
    inv_movements.confirm_with_client(db, item, current_user,
                                      location_id=location.id, reason=payload.reason)
    return _item_out(item, current_user, db)


@router.post("/items/{item_id}/return-from-client", response_model=ItemOut)
def return_from_client(item_id: int, payload: ReturnFromClientIn,
                       db: Session = Depends(get_db),
                       current_user: User = Depends(require_custodian)):
    item = _load_item(db, item_id)
    _assert_manages(item.domain, current_user, db)
    location = _load_location(db, payload.location_id)
    _assert_shelf_matches_ownership(item, location)
    inv_movements.return_from_client(db, item, current_user,
                                     location_id=location.id,
                                     condition=payload.condition.value,
                                     reference=payload.reference)
    return _item_out(item, current_user, db)


@router.post("/items/{item_id}/repair-out", response_model=ItemOut)
def repair_out(item_id: int, payload: RepairOutIn, db: Session = Depends(get_db),
               current_user: User = Depends(require_custodian)):
    item = _load_item(db, item_id)
    _assert_manages(item.domain, current_user, db)
    inv_movements.repair_out(db, item, current_user, reference=payload.reference,
                             reason=payload.reason)
    return _item_out(item, current_user, db)


@router.post("/items/{item_id}/repair-in", response_model=ItemOut)
def repair_in(item_id: int, payload: RepairInIn, db: Session = Depends(get_db),
              current_user: User = Depends(require_custodian)):
    item = _load_item(db, item_id)
    _assert_manages(item.domain, current_user, db)
    location = _load_location(db, payload.location_id)
    inv_movements.repair_in(db, item, current_user, location_id=location.id,
                            condition=payload.condition.value,
                            reference=payload.reference)
    return _item_out(item, current_user, db)


@router.post("/items/{item_id}/scrap", response_model=ItemOut)
def scrap_item(item_id: int, payload: ReasonIn, request: Request,
               db: Session = Depends(get_db),
               current_user: User = Depends(require_admin_or_hr)):
    """
    End of life, with a reason, and never by the custodian alone.

    The design gives the custodian "propose only" here. Rather than build a
    proposal queue nobody asked for, the endpoint is simply closed to them —
    a custodian who wants something scrapped asks an admin, which is the
    conversation the proposal queue would have produced anyway.
    """
    item = _load_item(db, item_id)
    inv_movements.scrap(db, item, current_user, reason=payload.reason)
    audit.record(db, "inventory.scrapped", actor=current_user, request=request,
                 target_type="inv_item", target_id=item.id,
                 detail={"tag": item.tag, "reason": payload.reason})
    return _item_out(item, current_user, db)


@router.post("/items/{item_id}/lost", response_model=ItemOut)
def mark_lost(item_id: int, payload: LostIn, request: Request,
              db: Session = Depends(get_db),
              current_user: User = Depends(require_admin_or_hr)):
    item = _load_item(db, item_id)
    inv_movements.mark_lost(db, item, current_user, reason=payload.reason,
                            recovery_amount=payload.recovery_amount)
    audit.record(db, "inventory.marked_lost", actor=current_user, request=request,
                 target_type="inv_item", target_id=item.id,
                 detail={"tag": item.tag, "reason": payload.reason})
    return _item_out(item, current_user, db)


@router.post("/items/{item_id}/found", response_model=ItemOut)
def mark_found(item_id: int, payload: FoundIn, request: Request,
               db: Session = Depends(get_db),
               current_user: User = Depends(require_custodian)):
    """It turned up. Closes the open loss rather than erasing it."""
    item = _load_item(db, item_id)
    _assert_manages(item.domain, current_user, db)
    location = _load_location(db, payload.location_id)
    inv_movements.mark_found(db, item, current_user, location_id=location.id,
                             reason=payload.reason)
    audit.record(db, "inventory.found", actor=current_user, request=request,
                 target_type="inv_item", target_id=item.id,
                 detail={"tag": item.tag, "location": location.code})
    return _item_out(item, current_user, db)


@router.post("/items/{item_id}/adjust", response_model=ItemOut)
def adjust_count(item_id: int, payload: AdjustIn, request: Request,
                 db: Session = Depends(get_db),
                 current_user: User = Depends(require_custodian)):
    """
    Correct a count.

    The only way the past is ever changed, and it is not an edit: the wrong
    number and the correction both stay readable, which is what lets a
    repeatedly drifting category be recognised as a process failure rather than
    tidied away one row at a time.
    """
    item = _load_item(db, item_id)
    _assert_manages(item.domain, current_user, db)
    inv_movements.audit_adjust(db, item, current_user, qty_delta=payload.qty_delta,
                               reason=payload.reason, reference=payload.reference)
    audit.record(db, "inventory.adjusted", actor=current_user, request=request,
                 target_type="inv_item", target_id=item.id,
                 detail={"tag": item.tag, "qty_delta": payload.qty_delta,
                         "reason": payload.reason})
    return _item_out(item, current_user, db)


@router.post("/items/{item_id}/write-off", response_model=ItemOut)
def write_off(item_id: int, payload: WriteOffIn, request: Request,
              db: Session = Depends(get_db),
              current_user: User = Depends(require_admin_or_hr)):
    """
    Give up on something a leaver never returned, and charge for it.

    The one way a clearance line closes without the hardware coming back. The
    amount lands on the final settlement as `asset_recovery`, so "written off"
    is a decision with a number attached rather than a checkbox someone ticked
    to unblock an exit.
    """
    item = _load_item(db, item_id)
    if not item.current_holder_id:
        raise _bad(f"{item.tag} is not with anybody. Mark it lost instead.")
    holder_id = item.current_holder_id
    inv_movements.write_off(db, item, current_user, reason=payload.reason,
                            recovery_amount=payload.recovery_amount)
    audit.record(db, "inventory.written_off", actor=current_user, request=request,
                 target_type="inv_item", target_id=item.id,
                 detail={"tag": item.tag, "holder_id": holder_id,
                         "reason": payload.reason,
                         "recovery_amount": payload.recovery_amount})
    return _item_out(item, current_user, db)


# --- Who holds what -------------------------------------------------------

@router.get("/employees/{employee_id}/holdings", response_model=List[ItemOut])
def employee_holdings(employee_id: int, db: Session = Depends(get_db),
                      current_user: User = Depends(get_current_user)):
    """
    What one person is holding.

    Visible to that person, their line manager and the custodian — a manager
    signing off a handover needs the list without needing HR rights.
    """
    employee = db.query(User).filter(User.id == employee_id).first()
    if not employee:
        raise _missing("Employee not found")
    allowed = (employee_id == current_user.id
               or employee.manager_id == current_user.id
               or is_custodian(current_user, db))
    if not allowed:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="Not allowed to view this employee's holdings")
    items = (db.query(InvItem)
             .options(joinedload(InvItem.category), joinedload(InvItem.location),
                      joinedload(InvItem.holder))
             .filter(InvItem.current_holder_id == employee_id)
             .order_by(InvItem.tag).all())
    return [_item_out(i, current_user, db) for i in items]


@router.get("/me/holdings", response_model=List[ItemOut])
def my_holdings(db: Session = Depends(get_db),
                current_user: User = Depends(get_current_user)):
    """What I am holding. This is the list I have to hand back when I leave."""
    items = (db.query(InvItem)
             .options(joinedload(InvItem.category), joinedload(InvItem.location))
             .filter(InvItem.current_holder_id == current_user.id)
             .order_by(InvItem.tag).all())
    return [_item_out(i, current_user, db) for i in items]


# --- Requests -------------------------------------------------------------

def _request_out(r: InvRequest) -> RequestOut:
    out = RequestOut.model_validate(r, from_attributes=True)
    out.requester_name = r.requester.full_name if r.requester else None
    out.approver_name = r.approver.full_name if r.approver else None
    if r.item:
        out.item_tag, out.item_name = r.item.tag, r.item.name
    out.category_name = r.category.name if r.category else None
    return out


@router.get("/requests", response_model=List[RequestOut])
def list_requests(status_filter: Optional[str] = Query(None, alias="status"),
                  mine: bool = False, db: Session = Depends(get_db),
                  current_user: User = Depends(get_current_user)):
    q = (db.query(InvRequest)
         .options(joinedload(InvRequest.requester), joinedload(InvRequest.approver),
                  joinedload(InvRequest.item), joinedload(InvRequest.category)))
    if status_filter:
        q = q.filter(InvRequest.status == status_filter)
    rows = q.order_by(InvRequest.created_at.desc()).all()
    if mine:
        rows = [r for r in rows if r.requester_id == current_user.id]
    elif not is_custodian(current_user, db):
        # A lead sees their own requests and the ones they may decide, not the
        # whole company's: the list is what the Approve buttons are drawn from.
        rows = [r for r in rows if r.requester_id == current_user.id
                or (current_user.role == RoleEnum.MANAGER
                    and can_approve_request_for(current_user, r.requester, db))]
    return [_request_out(r) for r in rows]


@router.post("/requests", response_model=RequestOut,
             status_code=status.HTTP_201_CREATED)
def create_request(payload: RequestIn, db: Session = Depends(get_db),
                   current_user: User = Depends(get_current_user)):
    if not payload.item_id and not payload.category_id:
        raise _bad("Ask for a specific item or for a category, not neither.")
    office = _bad("Office equipment is issued by admin or HR, not through a "
                  "stock request. Ask them directly.")
    if payload.item_id and _load_item(db, payload.item_id).is_office:
        raise office
    if payload.category_id:
        category = db.query(InvCategory).filter(
            InvCategory.id == payload.category_id).first()
        if not category:
            raise _missing("Category not found")
        if category.domain == CategoryDomainEnum.OFFICE.value:
            raise office

    row = InvRequest(**payload.model_dump(), requester_id=current_user.id,
                     status=RequestStatusEnum.PENDING.value)
    db.add(row)
    db.commit()
    db.refresh(row)

    # The lead hears immediately; the 48-hour nudge in the digest is the
    # backstop, not the first word.
    if current_user.manager_id:
        try:
            create_notification(
                db, current_user.manager_id, "Stock request awaiting your approval",
                f"{current_user.full_name} asked for {row.qty:g} x "
                f"{row.item.name if row.item else (row.category.name if row.category else 'an item')}.",
                NotificationTypeEnum.INFO, related_id=row.id,
                related_type="inventory_item")
        except Exception:
            db.rollback()
    return _request_out(row)


@router.post("/requests/{request_id}/decision", response_model=RequestOut)
def decide_request(request_id: int, payload: RequestDecisionIn, request: Request,
                   db: Session = Depends(get_db),
                   current_user: User = Depends(get_current_user)):
    """A lead approves for their own team; a custodian or admin for anybody."""
    row = (db.query(InvRequest)
           .options(joinedload(InvRequest.requester), joinedload(InvRequest.item),
                    joinedload(InvRequest.category))
           .filter(InvRequest.id == request_id).first())
    if not row:
        raise _missing("Request not found")
    if row.status != RequestStatusEnum.PENDING.value:
        raise _bad(f"This request is already {row.status}.")
    if row.requester_id == current_user.id and not is_custodian(current_user, db):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="You cannot approve your own request.")
    if not can_approve_request_for(current_user, row.requester, db):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="You do not approve requests for this person.")

    row.status = (RequestStatusEnum.APPROVED.value if payload.approved
                  else RequestStatusEnum.REJECTED.value)
    row.approver_id = current_user.id
    row.approved_at = datetime.utcnow()
    if not payload.approved:
        row.rejection_reason = payload.reason
    db.commit()
    db.refresh(row)

    try:
        create_notification(
            db, row.requester_id,
            f"Stock request {'approved' if payload.approved else 'declined'}",
            (f"{current_user.full_name} approved your request. The custodian "
             "will issue it." if payload.approved
             else f"{current_user.full_name} declined it."
                  + (f" Reason: {payload.reason}" if payload.reason else "")),
            NotificationTypeEnum.SUCCESS if payload.approved else NotificationTypeEnum.WARNING,
            related_id=row.id, related_type="inventory_item")
    except Exception:
        db.rollback()

    audit.record(db, f"inventory.request_{row.status}", actor=current_user,
                 request=request, target_type="inv_request", target_id=row.id,
                 detail={"requester_id": row.requester_id})
    return _request_out(row)


@router.post("/requests/{request_id}/issue", response_model=RequestOut)
def issue_request(request_id: int, payload: RequestIssueIn, request: Request,
                  db: Session = Depends(get_db),
                  current_user: User = Depends(require_custodian)):
    """
    Hand it over and close the request with the movement that satisfied it.

    The link is stored rather than inferred: six months later "what did we
    actually give them" is answerable in one hop instead of by matching
    timestamps.
    """
    row = (db.query(InvRequest)
           .options(joinedload(InvRequest.requester))
           .filter(InvRequest.id == request_id).first())
    if not row:
        raise _missing("Request not found")
    if row.status != RequestStatusEnum.APPROVED.value:
        raise _bad(f"This request is {row.status}; only an approved one can be issued.")

    item = _load_item(db, payload.item_id or row.item_id) if (
        payload.item_id or row.item_id) else None
    if item is None:
        raise _bad("This request named a category. Say which item is being issued.")
    _assert_manages(item.domain, current_user, db)
    requester = row.requester or _load_employee(db, row.requester_id)
    qty = payload.qty or row.qty

    if item.is_quantity_tracked:
        movement = inv_movements.consume(
            db, item, current_user, qty=qty, project_id=row.project_id,
            reason=f"Issued against request {row.id}")
    else:
        movement = inv_movements.checkout(
            db, item, current_user, requester,
            expected_return_date=payload.expected_return_date,
            reason=f"Issued against request {row.id}")

    row.status = RequestStatusEnum.ISSUED.value
    row.item_id = item.id
    row.issued_movement_id = movement.id
    db.commit()
    db.refresh(row)
    audit.record(db, "inventory.request_issued", actor=current_user, request=request,
                 target_type="inv_request", target_id=row.id,
                 detail={"item_id": item.id, "movement_id": movement.id, "qty": qty})
    return _request_out(row)


@router.post("/requests/{request_id}/cancel", response_model=RequestOut)
def cancel_request(request_id: int, db: Session = Depends(get_db),
                   current_user: User = Depends(get_current_user)):
    row = db.query(InvRequest).filter(InvRequest.id == request_id).first()
    if not row:
        raise _missing("Request not found")
    if row.requester_id != current_user.id and not is_custodian(current_user, db):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="Only the requester or a custodian can cancel this.")
    if row.status in (RequestStatusEnum.ISSUED.value, RequestStatusEnum.CANCELLED.value):
        raise _bad(f"This request is already {row.status}.")
    row.status = RequestStatusEnum.CANCELLED.value
    db.commit()
    db.refresh(row)
    return _request_out(row)


# --- Audit ----------------------------------------------------------------

def _session_out(session_row: InvAuditSession, with_lines: bool = True) -> AuditSessionOut:
    out = AuditSessionOut.model_validate(session_row, from_attributes=True)
    out.scope_location_code = (session_row.scope_location.code
                               if session_row.scope_location else None)
    out.scope_category_name = (session_row.scope_category.name
                               if session_row.scope_category else None)
    out.opened_by_name = session_row.opener.full_name if session_row.opener else None
    out.expected_count = len(session_row.lines)
    out.scanned_count = sum(1 for l in session_row.lines if l.scanned_at)
    if with_lines:
        lines = []
        for line in session_row.lines:
            row = AuditLineOut.model_validate(line, from_attributes=True)
            if line.item:
                row.item_tag, row.item_name = line.item.tag, line.item.name
            lines.append(row)
        out.lines = sorted(lines, key=lambda r: r.item_tag or "")
    else:
        out.lines = []
    return out


@router.get("/audit/sessions", response_model=List[AuditSessionOut])
def list_audit_sessions(status_filter: Optional[str] = Query(None, alias="status"),
                        db: Session = Depends(get_db),
                        current_user: User = Depends(require_custodian)):
    q = db.query(InvAuditSession)
    if status_filter:
        q = q.filter(InvAuditSession.status == status_filter)
    rows = q.order_by(InvAuditSession.opened_at.desc()).limit(100).all()
    return [_session_out(r, with_lines=False) for r in rows]


@router.post("/audit/sessions", response_model=AuditSessionOut,
             status_code=status.HTTP_201_CREATED)
def open_audit_session(payload: AuditSessionIn, db: Session = Depends(get_db),
                       current_user: User = Depends(require_custodian)):
    row = inv_audit.open_session(db, current_user, location_id=payload.location_id,
                                 category_id=payload.category_id, label=payload.label)
    return _session_out(row)


@router.get("/audit/sessions/{session_id}", response_model=AuditSessionOut)
def get_audit_session(session_id: int, db: Session = Depends(get_db),
                      current_user: User = Depends(require_custodian)):
    row = db.query(InvAuditSession).filter(InvAuditSession.id == session_id).first()
    if not row:
        raise _missing("Audit session not found")
    return _session_out(row)


@router.post("/audit/sessions/{session_id}/scan", response_model=AuditLineOut)
def scan_during_audit(session_id: int, payload: AuditScanIn,
                      db: Session = Depends(get_db),
                      current_user: User = Depends(require_custodian)):
    row = db.query(InvAuditSession).filter(InvAuditSession.id == session_id).first()
    if not row:
        raise _missing("Audit session not found")
    line = inv_audit.scan(db, row, current_user, tag=payload.tag,
                          counted_qty=payload.counted_qty, note=payload.note)
    out = AuditLineOut.model_validate(line, from_attributes=True)
    if line.item:
        out.item_tag, out.item_name = line.item.tag, line.item.name
    return out


@router.post("/audit/sessions/{session_id}/close")
def close_audit_session(session_id: int, payload: AuditCloseIn, request: Request,
                        db: Session = Depends(get_db),
                        current_user: User = Depends(require_custodian)):
    """
    Close the count, and turn everything unscanned into a variance.

    Variances are reported as process failures, not as theft. Naming culprits
    stops people reporting honestly, and honest reporting is the only thing
    that makes the count worth doing.
    """
    row = db.query(InvAuditSession).filter(InvAuditSession.id == session_id).first()
    if not row:
        raise _missing("Audit session not found")
    result = inv_audit.close_session(db, row, current_user, notes=payload.notes)

    if result["variances"]:
        from app.utils.inv_alerts import _custodian_recipients
        for person in _custodian_recipients(db):
            try:
                create_notification(
                    db, person.id, "Audit variance logged",
                    f"Session {row.id} closed with {result['variances']} "
                    f"variance(s) worth {settings.CURRENCY} "
                    f"{result['variance_value']:,.0f}.",
                    NotificationTypeEnum.WARNING, related_id=row.id,
                    related_type="inventory_item")
            except Exception:
                db.rollback()

    audit.record(db, "inventory.audit_closed", actor=current_user, request=request,
                 target_type="inv_audit_session", target_id=row.id, detail=result)
    return result


# --- Reports --------------------------------------------------------------

@router.get("/reports/by-holder", response_model=List[HolderRow])
def report_by_holder(domain: Optional[str] = None, db: Session = Depends(get_db),
                     current_user: User = Depends(require_custodian)):
    """Who has what, and for how long."""
    if not is_privileged(current_user, db):
        domain = CategoryDomainEnum.LAB.value
    q = (db.query(InvItem)
         .options(joinedload(InvItem.holder), joinedload(InvItem.category),
                  joinedload(InvItem.location)))
    items = _domain_filter(q, domain).filter(InvItem.current_holder_id.isnot(None)).all()
    grouped = {}
    today = date.today()
    for item in items:
        row = grouped.setdefault(item.current_holder_id, {
            "employee_id": item.current_holder_id,
            "employee_name": item.holder.full_name if item.holder else "Unknown",
            "items": [], "overdue": 0, "value": 0.0})
        row["items"].append(item)
        if item.hold_due_date and item.hold_due_date < today:
            row["overdue"] += 1
        row["value"] += float(item.replacement_cost or item.purchase_cost or 0.0)
    return [HolderRow(employee_id=r["employee_id"], employee_name=r["employee_name"],
                      item_count=len(r["items"]), overdue_count=r["overdue"],
                      total_value=round(r["value"], 2),
                      items=[_item_out(i, current_user, db) for i in r["items"]])
            for r in sorted(grouped.values(), key=lambda r: -len(r["items"]))]


@router.get("/reports/overdue", response_model=List[ItemOut])
def report_overdue(domain: Optional[str] = None, db: Session = Depends(get_db),
                   current_user: User = Depends(require_custodian)):
    """What should have come back."""
    if not is_privileged(current_user, db):
        domain = CategoryDomainEnum.LAB.value
    items = (_domain_filter(db.query(InvItem)
             .options(joinedload(InvItem.holder), joinedload(InvItem.category)), domain)
             .filter(InvItem.hold_due_date.isnot(None),
                     InvItem.hold_due_date < date.today())
             .order_by(InvItem.hold_due_date).all())
    return [_item_out(i, current_user, db) for i in items]


@router.get("/reports/low-stock", response_model=List[StockRow])
def report_low_stock(db: Session = Depends(get_db),
                     current_user: User = Depends(require_custodian)):
    """What to reorder."""
    items = (db.query(InvItem)
             .options(joinedload(InvItem.location))
             .filter(InvItem.min_qty.isnot(None), InvItem.qty <= InvItem.min_qty)
             .order_by(InvItem.name).all())
    return [StockRow(item_id=i.id, tag=i.tag, name=i.name, qty=i.qty,
                     min_qty=i.min_qty,
                     location_code=i.location.code if i.location else None)
            for i in items]


@router.get("/reports/project-cost", response_model=List[ProjectCostRow])
def report_project_cost(db: Session = Depends(get_db),
                        current_user: User = Depends(require_admin_or_hr)):
    """
    Real material cost per project, from the consume log.

    Quietly the highest-value output of the whole module: this number feeds
    quoting directly and is otherwise a guess.
    """
    rows = (db.query(InvMovement.project_id,
                     func.sum(InvMovement.qty_delta * func.coalesce(InvMovement.unit_cost, 0.0)),
                     func.count(InvMovement.id))
            .filter(InvMovement.movement_type == MovementTypeEnum.CONSUME.value)
            .group_by(InvMovement.project_id).all())
    names = {}
    for item in db.query(InvItem).filter(InvItem.project_id.isnot(None)).all():
        names.setdefault(item.project_id, (item.project_code, item.project_name))
    out = []
    for project_id, total, count in rows:
        code, name = names.get(project_id, (None, None))
        out.append(ProjectCostRow(project_id=project_id, project_code=code,
                                  project_name=name,
                                  material_cost=round(abs(float(total or 0.0)), 2),
                                  line_count=count))
    return sorted(out, key=lambda r: -r.material_cost)


@router.get("/reports/client-hardware", response_model=List[ItemOut])
def report_client_hardware(db: Session = Depends(get_db),
                           current_user: User = Depends(require_admin_or_hr)):
    """Our exposure on client property."""
    items = (db.query(InvItem)
             .options(joinedload(InvItem.location), joinedload(InvItem.category))
             .filter(InvItem.ownership.in_([OwnershipEnum.CLIENT.value,
                                            OwnershipEnum.LOANED_IN.value]))
             .order_by(InvItem.client_name, InvItem.tag).all())
    return [_item_out(i, current_user, db) for i in items]


@router.get("/reports/idle", response_model=List[ItemOut])
def report_idle(days: int = Query(None, ge=1), domain: Optional[str] = None,
                db: Session = Depends(get_db),
                current_user: User = Depends(require_admin_or_hr)):
    """
    Equipment nothing has happened to in months.

    Candidates for reallocation or sale — the question "what are we storing
    that nobody uses" has no other way of being asked.
    """
    horizon = datetime.utcnow() - timedelta(days=days or settings.INV_IDLE_ASSET_DAYS)
    last_move = dict(db.query(InvMovement.item_id, func.max(InvMovement.created_at))
                     .group_by(InvMovement.item_id).all())
    items = (_domain_filter(db.query(InvItem)
             .options(joinedload(InvItem.location), joinedload(InvItem.category)), domain)
             .filter(InvItem.status == ItemStatusEnum.IN_STOCK.value).all())
    idle = [i for i in items
            if (last_move.get(i.id) or i.created_at or datetime.utcnow()) < horizon]
    return [_item_out(i, current_user, db) for i in sorted(idle, key=lambda i: i.tag)]


@router.get("/reports/register", response_model=List[ItemOut])
def report_register(domain: Optional[str] = None, db: Session = Depends(get_db),
                    current_user: User = Depends(require_admin_or_hr)):
    """What we own and what it is worth. Admin and HR only — it carries costs."""
    items = (_domain_filter(db.query(InvItem)
             .options(joinedload(InvItem.category), joinedload(InvItem.location),
                      joinedload(InvItem.holder)), domain)
             .filter(~InvItem.status.in_([ItemStatusEnum.SCRAPPED.value]))
             .order_by(InvItem.category_id, InvItem.tag).all())
    return [_item_out(i, current_user, db) for i in items]
