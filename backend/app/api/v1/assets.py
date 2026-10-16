"""
The old asset-register API, kept alive against the new inventory tables.

DEPRECATED. Everything here is served out of `inv_items` and `inv_movements`;
the `assets` / `asset_assignments` pair it was written against no longer
exists. It stays mounted for one release so the portal does not have to move in
the same deploy as the backend, and every response carries a `Deprecation`
header pointing at the replacement.

Where the two vocabularies disagree, this translates back to the old one:

    status     under_repair -> in_repair, scrapped -> retired,
               in_transit / with_client -> issued (out of the building)
    condition  working -> good, faulty -> damaged, scrapped -> damaged
    category   the category's name, lowercased and underscored, which is
               exactly the old enum value

Use /api/v1/inventory instead. It is a superset of this, and this file goes
away once the portal has moved.
"""
from datetime import date, datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session, joinedload

from app.api.deps import get_current_user, is_privileged, require_admin_or_hr
from app.database import get_db
from app.models.inventory import (ConditionEnum, InvCategory, InvItem,
                                  InvLocation, InvMovement, ItemStatusEnum,
                                  LocationTypeEnum, MovementTypeEnum)
from app.models.user import User
from app.utils import audit, inv_movements, inv_tags

router = APIRouter()

DEPRECATION = {"Deprecation": "true", "Link": '</api/v1/inventory>; rel="successor-version"'}

_STATUS_TO_LEGACY = {
    ItemStatusEnum.IN_STOCK.value: "in_stock",
    ItemStatusEnum.ISSUED.value: "issued",
    ItemStatusEnum.IN_TRANSIT.value: "issued",
    ItemStatusEnum.WITH_CLIENT.value: "issued",
    ItemStatusEnum.UNDER_REPAIR.value: "in_repair",
    ItemStatusEnum.LOST.value: "lost",
    ItemStatusEnum.SCRAPPED.value: "retired",
}
_STATUS_FROM_LEGACY = {
    "in_stock": ItemStatusEnum.IN_STOCK.value,
    "issued": ItemStatusEnum.ISSUED.value,
    "in_repair": ItemStatusEnum.UNDER_REPAIR.value,
    "retired": ItemStatusEnum.SCRAPPED.value,
    "lost": ItemStatusEnum.LOST.value,
}
_CONDITION_TO_LEGACY = {
    ConditionEnum.NEW.value: "new",
    ConditionEnum.WORKING.value: "good",
    ConditionEnum.FAULTY.value: "damaged",
    ConditionEnum.SCRAPPED.value: "damaged",
}
_CONDITION_FROM_LEGACY = {
    "new": ConditionEnum.NEW.value,
    "good": ConditionEnum.WORKING.value,
    "fair": ConditionEnum.WORKING.value,
    "damaged": ConditionEnum.FAULTY.value,
}


def _legacy_category(category: Optional[InvCategory]) -> str:
    return (category.name.lower().replace(" ", "_").replace("&", "and")
            if category else "other")


class AssetCreate(BaseModel):
    tag: str = Field(..., min_length=1, max_length=64)
    category: str
    name: str = Field(..., min_length=1, max_length=200)
    description: Optional[str] = None
    serial_number: Optional[str] = Field(None, max_length=120)
    purchase_date: Optional[date] = None
    purchase_cost: Optional[float] = Field(None, ge=0)
    replacement_cost: Optional[float] = Field(None, ge=0)
    condition: str = "good"
    notes: Optional[str] = None


class AssetUpdate(BaseModel):
    tag: Optional[str] = Field(None, min_length=1, max_length=64)
    category: Optional[str] = None
    name: Optional[str] = Field(None, min_length=1, max_length=200)
    description: Optional[str] = None
    serial_number: Optional[str] = Field(None, max_length=120)
    purchase_date: Optional[date] = None
    purchase_cost: Optional[float] = Field(None, ge=0)
    replacement_cost: Optional[float] = Field(None, ge=0)
    status: Optional[str] = None
    condition: Optional[str] = None
    notes: Optional[str] = None


class IssueRequest(BaseModel):
    employee_id: int
    condition_out: str = "good"
    expected_return_date: Optional[date] = None
    notes: Optional[str] = None


class ReturnRequest(BaseModel):
    condition_in: str = "good"
    status_after: str = "in_stock"
    recovery_amount: Optional[float] = Field(None, ge=0)
    notes: Optional[str] = None


class AssignmentOut(BaseModel):
    id: int
    asset_id: int
    employee_id: int
    employee_name: Optional[str] = None
    asset_tag: Optional[str] = None
    asset_name: Optional[str] = None
    issued_at: datetime
    condition_out: str
    expected_return_date: Optional[date] = None
    returned_at: Optional[datetime] = None
    condition_in: Optional[str] = None
    recovery_amount: Optional[float] = None
    notes: Optional[str] = None


class AssetOut(BaseModel):
    id: int
    tag: str
    category: str
    name: str
    description: Optional[str] = None
    serial_number: Optional[str] = None
    purchase_date: Optional[date] = None
    purchase_cost: Optional[float] = None
    replacement_cost: Optional[float] = None
    status: str
    condition: str
    location: Optional[str] = None
    notes: Optional[str] = None
    held_by_id: Optional[int] = None
    held_by_name: Optional[str] = None
    issued_at: Optional[datetime] = None


def _out(item: InvItem) -> AssetOut:
    return AssetOut(
        id=item.id, tag=item.tag, category=_legacy_category(item.category),
        name=item.name, description=item.description,
        serial_number=item.serial_number, purchase_date=item.purchase_date,
        purchase_cost=item.purchase_cost, replacement_cost=item.replacement_cost,
        status=_STATUS_TO_LEGACY.get(item.status, item.status),
        condition=_CONDITION_TO_LEGACY.get(item.condition, item.condition),
        location=item.location.code if item.location else None,
        notes=item.notes, held_by_id=item.current_holder_id,
        held_by_name=item.holder.full_name if item.holder else None,
        issued_at=item.held_since)


def _pair_assignments(db: Session, movements: List[InvMovement]) -> List[AssignmentOut]:
    """
    Reassemble issue-and-return pairs out of the append-only log.

    The old table stored a hold as one row that got updated on return. The log
    stores two events. Pairing them back up is what lets the old endpoints keep
    their shape without the old table existing.
    """
    out: List[AssignmentOut] = []
    open_holds = {}
    for m in sorted(movements, key=lambda m: (m.created_at, m.id)):
        if m.movement_type in (MovementTypeEnum.CHECKOUT.value,
                               MovementTypeEnum.TRANSFER.value):
            if m.from_holder_id in open_holds:
                # A transfer closes the previous hold and opens a new one.
                open_holds.pop(m.from_holder_id).returned_at = m.created_at
            row = AssignmentOut(
                id=m.id, asset_id=m.item_id, employee_id=m.to_holder_id,
                employee_name=m.to_holder.full_name if m.to_holder else None,
                asset_tag=m.item.tag if m.item else None,
                asset_name=m.item.name if m.item else None,
                issued_at=m.created_at,
                condition_out=_CONDITION_TO_LEGACY.get(m.condition or "", "good"),
                expected_return_date=m.expected_return_date, notes=m.reason)
            open_holds[m.to_holder_id] = row
            out.append(row)
        elif m.movement_type in (MovementTypeEnum.CHECKIN.value,
                                 MovementTypeEnum.LOST.value,
                                 MovementTypeEnum.SCRAP.value):
            row = open_holds.pop(m.from_holder_id, None)
            if row is not None:
                row.returned_at = m.created_at
                row.condition_in = _CONDITION_TO_LEGACY.get(m.condition or "", None)
                row.recovery_amount = m.recovery_amount
    return out


def _holdings_as_assignments(db: Session, employee_id: int) -> List[AssignmentOut]:
    """Open holds only — the fast path, and the one the portal actually calls."""
    rows = []
    for item in inv_movements.holdings_for(db, employee_id):
        last = (db.query(InvMovement)
                .filter(InvMovement.item_id == item.id,
                        InvMovement.to_holder_id == employee_id)
                .order_by(InvMovement.created_at.desc()).first())
        rows.append(AssignmentOut(
            id=last.id if last else item.id, asset_id=item.id,
            employee_id=employee_id,
            employee_name=item.holder.full_name if item.holder else None,
            asset_tag=item.tag, asset_name=item.name,
            issued_at=item.held_since or (last.created_at if last else datetime.utcnow()),
            # Off the movement, not off the item: the point of recording it at
            # handover is that it does not change when the item's does.
            condition_out=_CONDITION_TO_LEGACY.get(
                (last.condition if last else None) or item.condition, "good"),
            expected_return_date=item.hold_due_date,
            notes=last.reason if last else None))
    return rows


def open_assignments_for(db: Session, employee_id: int):
    """
    Kept as a name because callers outside this module imported it.

    New code should call inv_movements.holdings_for, which returns items rather
    than the old assignment rows.
    """
    return inv_movements.holdings_for(db, employee_id)


def _load(db: Session, asset_id: int) -> InvItem:
    item = (db.query(InvItem)
            .options(joinedload(InvItem.category), joinedload(InvItem.holder),
                     joinedload(InvItem.location))
            .filter(InvItem.id == asset_id).first())
    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="Asset not found")
    return item


def _default_location(db: Session) -> InvLocation:
    """
    Where the old return endpoint puts things, since it never took a location.

    Prefers a location coded STORE, falls back to any internal one. If the
    location tree has not been set up at all this fails loudly rather than
    inventing a shelf.
    """
    loc = db.query(InvLocation).filter(InvLocation.code == "STORE",
                                       InvLocation.is_active == True).first()  # noqa: E712
    if loc:
        return loc
    loc = (db.query(InvLocation)
           .filter(InvLocation.location_type == LocationTypeEnum.INTERNAL.value,
                   InvLocation.is_active == True)                              # noqa: E712
           .order_by(InvLocation.id).first())
    if not loc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No storage location has been set up. Create one under "
                   "/api/v1/inventory/locations first.")
    return loc


@router.get("", response_model=List[AssetOut])
def list_assets(
    response: Response,
    status_filter: Optional[str] = Query(None, alias="status"),
    category: Optional[str] = None,
    held_by: Optional[int] = Query(None, description="Employee id currently holding it"),
    search: Optional[str] = Query(None, description="Matches tag, name or serial"),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin_or_hr),
):
    response.headers.update(DEPRECATION)
    q = (db.query(InvItem)
         .options(joinedload(InvItem.category), joinedload(InvItem.holder),
                  joinedload(InvItem.location)))
    if status_filter:
        q = q.filter(InvItem.status == _STATUS_FROM_LEGACY.get(status_filter,
                                                               status_filter))
    if held_by is not None:
        q = q.filter(InvItem.current_holder_id == held_by)
    if search:
        like = f"%{search.strip()}%"
        q = q.filter(InvItem.tag.ilike(like) | InvItem.name.ilike(like)
                     | InvItem.serial_number.ilike(like))
    items = q.order_by(InvItem.category_id, InvItem.tag).all()
    if category:
        items = [i for i in items if _legacy_category(i.category) == category]
    return [_out(i) for i in items]


@router.get("/me", response_model=List[AssignmentOut])
def my_assets(response: Response, db: Session = Depends(get_db),
              current_user: User = Depends(get_current_user)):
    response.headers.update(DEPRECATION)
    return _holdings_as_assignments(db, current_user.id)


@router.get("/employee/{employee_id}", response_model=List[AssignmentOut])
def employee_assets(employee_id: int, response: Response,
                    include_returned: bool = False, db: Session = Depends(get_db),
                    current_user: User = Depends(get_current_user)):
    response.headers.update(DEPRECATION)
    employee = db.query(User).filter(User.id == employee_id).first()
    if not employee:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="Employee not found")
    allowed = (employee_id == current_user.id
               or employee.manager_id == current_user.id
               or is_privileged(current_user, db))
    if not allowed:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="Not allowed to view this employee's assets")
    if not include_returned:
        return _holdings_as_assignments(db, employee_id)

    movements = (db.query(InvMovement)
                 .options(joinedload(InvMovement.item),
                          joinedload(InvMovement.to_holder))
                 .filter((InvMovement.to_holder_id == employee_id)
                         | (InvMovement.from_holder_id == employee_id))
                 .all())
    rows = _pair_assignments(db, movements)
    return [r for r in rows if r.employee_id == employee_id]


@router.post("", response_model=AssetOut, status_code=status.HTTP_201_CREATED)
def create_asset(payload: AssetCreate, request: Request, response: Response,
                 db: Session = Depends(get_db),
                 current_user: User = Depends(require_admin_or_hr)):
    response.headers.update(DEPRECATION)
    if not inv_tags.is_available(db, payload.tag):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail=f"Asset tag '{payload.tag}' is already in use")
    category = None
    for c in db.query(InvCategory).all():
        if _legacy_category(c) == payload.category:
            category = c
            break
    if category is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail=f"Unknown category '{payload.category}'")

    item = InvItem(
        tag=payload.tag, category_id=category.id, name=payload.name,
        description=payload.description, serial_number=payload.serial_number,
        purchase_date=payload.purchase_date, purchase_cost=payload.purchase_cost,
        replacement_cost=payload.replacement_cost, notes=payload.notes,
        condition=_CONDITION_FROM_LEGACY.get(payload.condition,
                                             ConditionEnum.WORKING.value),
        status=ItemStatusEnum.IN_STOCK.value, created_by=current_user.id,
        current_location_id=_default_location(db).id)
    db.add(item)
    db.commit()
    db.refresh(item)
    audit.record(db, "asset.created", actor=current_user, request=request,
                 target_type="inv_item", target_id=item.id,
                 detail={"tag": item.tag, "name": item.name, "via": "legacy api"})
    return _out(item)


@router.put("/{asset_id}", response_model=AssetOut)
def update_asset(asset_id: int, payload: AssetUpdate, request: Request,
                 response: Response, db: Session = Depends(get_db),
                 current_user: User = Depends(require_admin_or_hr)):
    response.headers.update(DEPRECATION)
    item = _load(db, asset_id)
    changes = payload.model_dump(exclude_unset=True)

    if "tag" in changes and changes["tag"] != item.tag:
        if not inv_tags.is_available(db, changes["tag"]):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                                detail=f"Asset tag '{changes['tag']}' is already in use")
    if "category" in changes:
        wanted = changes.pop("category")
        match = next((c for c in db.query(InvCategory).all()
                      if _legacy_category(c) == wanted), None)
        if match is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                                detail=f"Unknown category '{wanted}'")
        changes["category_id"] = match.id
    if "status" in changes:
        wanted = _STATUS_FROM_LEGACY.get(changes.pop("status"))
        if wanted == ItemStatusEnum.ISSUED.value and not item.current_holder_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Issue the asset to someone rather than setting it to issued")
        if item.current_holder_id and wanted != ItemStatusEnum.ISSUED.value:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="This asset is out with someone. Record its return first.")
    if "condition" in changes:
        changes["condition"] = _CONDITION_FROM_LEGACY.get(
            changes["condition"], ConditionEnum.WORKING.value)

    for field, value in changes.items():
        setattr(item, field, value)
    db.commit()
    db.refresh(item)
    audit.record(db, "asset.updated", actor=current_user, request=request,
                 target_type="inv_item", target_id=item.id, detail=changes)
    return _out(item)


@router.delete("/{asset_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_asset(asset_id: int, request: Request, db: Session = Depends(get_db),
                 current_user: User = Depends(require_admin_or_hr)):
    item = _load(db, asset_id)
    if db.query(InvMovement).filter(InvMovement.item_id == item.id).count():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This asset has a movement history. Set its status to retired instead.")
    tag = item.tag
    db.delete(item)
    db.commit()
    audit.record(db, "asset.deleted", actor=current_user, request=request,
                 target_type="inv_item", target_id=asset_id, detail={"tag": tag})


@router.post("/{asset_id}/issue", response_model=AssetOut)
def issue_asset(asset_id: int, payload: IssueRequest, request: Request,
                response: Response, db: Session = Depends(get_db),
                current_user: User = Depends(require_admin_or_hr)):
    response.headers.update(DEPRECATION)
    item = _load(db, asset_id)
    employee = db.query(User).filter(User.id == payload.employee_id).first()
    if not employee:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="Employee not found")
    inv_movements.checkout(db, item, current_user, employee,
                           expected_return_date=payload.expected_return_date,
                           condition=_CONDITION_FROM_LEGACY.get(payload.condition_out),
                           reason=payload.notes)
    audit.record(db, "asset.issued", actor=current_user, request=request,
                 target_type="inv_item", target_id=item.id,
                 detail={"tag": item.tag, "employee_id": employee.id})
    db.refresh(item)
    return _out(item)


@router.post("/{asset_id}/return", response_model=AssetOut)
def return_asset(asset_id: int, payload: ReturnRequest, request: Request,
                 response: Response, db: Session = Depends(get_db),
                 current_user: User = Depends(require_admin_or_hr)):
    response.headers.update(DEPRECATION)
    item = _load(db, asset_id)
    if not item.current_holder_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="This asset is not currently issued to anyone")
    holder_id = item.current_holder_id
    condition = _CONDITION_FROM_LEGACY.get(payload.condition_in,
                                           ConditionEnum.WORKING.value)
    inv_movements.checkin(db, item, current_user,
                          location_id=_default_location(db).id,
                          condition=condition,
                          recovery_amount=payload.recovery_amount,
                          reason=payload.notes)
    audit.record(db, "asset.returned", actor=current_user, request=request,
                 target_type="inv_item", target_id=item.id,
                 detail={"tag": item.tag, "employee_id": holder_id,
                         "condition_in": payload.condition_in,
                         "recovery_amount": payload.recovery_amount})
    db.refresh(item)
    return _out(item)


@router.get("/{asset_id}/history", response_model=List[AssignmentOut])
def asset_history(asset_id: int, response: Response, db: Session = Depends(get_db),
                  current_user: User = Depends(require_admin_or_hr)):
    response.headers.update(DEPRECATION)
    _load(db, asset_id)
    movements = (db.query(InvMovement)
                 .options(joinedload(InvMovement.item),
                          joinedload(InvMovement.to_holder))
                 .filter(InvMovement.item_id == asset_id).all())
    return list(reversed(_pair_assignments(db, movements)))
