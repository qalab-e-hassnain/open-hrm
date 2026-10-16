"""Request and response shapes for the lab inventory module."""
from datetime import date, datetime
from typing import List, Optional

from pydantic import BaseModel, Field

from app.models.inventory import (CategoryDomainEnum, AuditOutcomeEnum, AuditSessionStatusEnum,
                                  ConditionEnum, ItemStatusEnum,
                                  LifecycleStateEnum, LocationTypeEnum,
                                  MovementTypeEnum, OwnershipEnum,
                                  RequestStatusEnum, TrackingTypeEnum)


# --- Categories -----------------------------------------------------------

class CategoryIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    tracking_type: TrackingTypeEnum = TrackingTypeEnum.SERIAL
    tag_prefix: Optional[str] = Field(None, max_length=16)
    serial_required: bool = False
    default_audit_days: int = Field(30, ge=1, le=730)
    domain: CategoryDomainEnum = CategoryDomainEnum.LAB
    is_active: bool = True


class CategoryUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=120)
    tracking_type: Optional[TrackingTypeEnum] = None
    tag_prefix: Optional[str] = Field(None, max_length=16)
    serial_required: Optional[bool] = None
    default_audit_days: Optional[int] = Field(None, ge=1, le=730)
    domain: Optional[CategoryDomainEnum] = None
    is_active: Optional[bool] = None


class CategoryOut(BaseModel):
    id: int
    name: str
    tracking_type: str
    tag_prefix: Optional[str] = None
    serial_required: bool
    default_audit_days: int
    domain: str = CategoryDomainEnum.LAB.value
    is_active: bool
    item_count: int = 0

    class Config:
        from_attributes = True


# --- Locations ------------------------------------------------------------

class LocationIn(BaseModel):
    code: str = Field(..., min_length=1, max_length=64)
    name: str = Field(..., min_length=1, max_length=160)
    parent_id: Optional[int] = None
    location_type: LocationTypeEnum = LocationTypeEnum.INTERNAL
    client_id: Optional[int] = None
    client_name: Optional[str] = None
    notes: Optional[str] = None


class LocationUpdate(BaseModel):
    code: Optional[str] = Field(None, min_length=1, max_length=64)
    name: Optional[str] = Field(None, min_length=1, max_length=160)
    parent_id: Optional[int] = None
    location_type: Optional[LocationTypeEnum] = None
    client_id: Optional[int] = None
    client_name: Optional[str] = None
    is_active: Optional[bool] = None
    notes: Optional[str] = None


class LocationOut(BaseModel):
    id: int
    code: str
    name: str
    path: str = ""
    parent_id: Optional[int] = None
    location_type: str
    client_id: Optional[int] = None
    client_name: Optional[str] = None
    is_active: bool
    item_count: int = 0

    class Config:
        from_attributes = True


# --- Items ----------------------------------------------------------------

class ItemIn(BaseModel):
    category_id: int
    name: str = Field(..., min_length=1, max_length=200)
    # Left out and the system issues one from the category's prefix. Supplied,
    # and it is used as given — which is how a machine that already has a
    # sticker on it keeps the number printed on the sticker.
    tag: Optional[str] = Field(None, min_length=1, max_length=64)
    manufacturer: Optional[str] = Field(None, max_length=120)
    mpn: Optional[str] = Field(None, max_length=120)
    description: Optional[str] = None
    serial_number: Optional[str] = Field(None, max_length=120)

    purchase_date: Optional[date] = None
    purchase_cost: Optional[float] = Field(None, ge=0)
    currency: Optional[str] = Field(None, max_length=8)
    supplier: Optional[str] = Field(None, max_length=160)
    invoice_ref: Optional[str] = Field(None, max_length=120)
    replacement_cost: Optional[float] = Field(None, ge=0)
    unit_cost: Optional[float] = Field(None, ge=0)

    condition: ConditionEnum = ConditionEnum.WORKING
    ownership: OwnershipEnum = OwnershipEnum.COMPANY
    lifecycle_state: Optional[LifecycleStateEnum] = None

    client_id: Optional[int] = None
    client_name: Optional[str] = Field(None, max_length=160)
    project_id: Optional[int] = None
    project_code: Optional[str] = Field(None, max_length=64)
    project_name: Optional[str] = Field(None, max_length=200)

    current_location_id: Optional[int] = None
    qty: float = Field(1.0, ge=0)
    min_qty: Optional[float] = Field(None, ge=0)

    photo_url: Optional[str] = None
    datasheet_url: Optional[str] = None
    notes: Optional[str] = None


class ItemUpdate(BaseModel):
    category_id: Optional[int] = None
    name: Optional[str] = Field(None, min_length=1, max_length=200)
    tag: Optional[str] = Field(None, min_length=1, max_length=64)
    manufacturer: Optional[str] = Field(None, max_length=120)
    mpn: Optional[str] = Field(None, max_length=120)
    description: Optional[str] = None
    serial_number: Optional[str] = Field(None, max_length=120)
    purchase_date: Optional[date] = None
    purchase_cost: Optional[float] = Field(None, ge=0)
    currency: Optional[str] = Field(None, max_length=8)
    supplier: Optional[str] = Field(None, max_length=160)
    invoice_ref: Optional[str] = Field(None, max_length=120)
    replacement_cost: Optional[float] = Field(None, ge=0)
    unit_cost: Optional[float] = Field(None, ge=0)
    ownership: Optional[OwnershipEnum] = None
    lifecycle_state: Optional[LifecycleStateEnum] = None
    client_id: Optional[int] = None
    client_name: Optional[str] = Field(None, max_length=160)
    project_id: Optional[int] = None
    project_code: Optional[str] = Field(None, max_length=64)
    project_name: Optional[str] = Field(None, max_length=200)
    min_qty: Optional[float] = Field(None, ge=0)
    photo_url: Optional[str] = None
    datasheet_url: Optional[str] = None
    notes: Optional[str] = None


class ItemOut(BaseModel):
    id: int
    tag: str
    name: str
    category_id: int
    category_name: Optional[str] = None
    # 'lab' or 'office': which section of the portal this belongs to.
    domain: Optional[str] = None
    tracking_type: Optional[str] = None
    manufacturer: Optional[str] = None
    mpn: Optional[str] = None
    description: Optional[str] = None
    serial_number: Optional[str] = None

    status: str
    condition: str
    ownership: str
    lifecycle_state: Optional[str] = None

    # Null for anyone without admin, HR or custodian rights. The register is
    # visible to everybody; what things cost is not.
    purchase_date: Optional[date] = None
    purchase_cost: Optional[float] = None
    replacement_cost: Optional[float] = None
    unit_cost: Optional[float] = None
    currency: Optional[str] = None
    supplier: Optional[str] = None
    invoice_ref: Optional[str] = None

    client_id: Optional[int] = None
    client_name: Optional[str] = None
    project_id: Optional[int] = None
    project_code: Optional[str] = None
    project_name: Optional[str] = None

    current_location_id: Optional[int] = None
    location_code: Optional[str] = None
    location_path: Optional[str] = None
    current_holder_id: Optional[int] = None
    holder_name: Optional[str] = None
    hold_due_date: Optional[date] = None
    days_held: Optional[int] = None
    is_overdue: bool = False

    qty: float = 1.0
    min_qty: Optional[float] = None
    photo_url: Optional[str] = None
    datasheet_url: Optional[str] = None
    notes: Optional[str] = None
    created_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class ScanOut(BaseModel):
    """
    Everything the scan screen renders, in one round trip.

    The five-second target in section 5 is the difference between a system
    people use and a system people walk around, and it is lost immediately if
    the screen has to ask a second time who the holder is or what this caller
    is allowed to do.
    """
    item: ItemOut
    actions: List[str]
    held_by_me: bool = False


class MovementOut(BaseModel):
    id: int
    item_id: int
    item_tag: Optional[str] = None
    item_name: Optional[str] = None
    movement_type: str
    from_location_id: Optional[int] = None
    to_location_id: Optional[int] = None
    from_location_code: Optional[str] = None
    to_location_code: Optional[str] = None
    from_holder_id: Optional[int] = None
    to_holder_id: Optional[int] = None
    from_holder_name: Optional[str] = None
    to_holder_name: Optional[str] = None
    qty_delta: float = 0.0
    project_id: Optional[int] = None
    unit_cost: Optional[float] = None
    performed_by: Optional[int] = None
    performed_by_name: Optional[str] = None
    reason: Optional[str] = None
    reference: Optional[str] = None
    expected_return_date: Optional[date] = None
    condition: Optional[str] = None
    recovery_amount: Optional[float] = None
    created_at: datetime

    class Config:
        from_attributes = True


# --- Movement inputs ------------------------------------------------------

class ReceiveIn(BaseModel):
    location_id: int
    qty: float = Field(1.0, gt=0)
    reference: Optional[str] = Field(None, max_length=120)
    condition: ConditionEnum = ConditionEnum.NEW


class CheckoutIn(BaseModel):
    # Left out, it is checked out to the caller — the self-service case, which
    # is the common one at the scan screen.
    employee_id: Optional[int] = None
    expected_return_date: Optional[date] = None
    # Left out, the item's recorded condition is carried onto the movement.
    condition: Optional[ConditionEnum] = None
    reason: Optional[str] = None


class CheckinIn(BaseModel):
    location_id: int
    condition: ConditionEnum = ConditionEnum.WORKING
    recovery_amount: Optional[float] = Field(None, ge=0)
    reason: Optional[str] = None


class TransferIn(BaseModel):
    to_employee_id: int
    expected_return_date: Optional[date] = None
    reason: Optional[str] = None


class RenewIn(BaseModel):
    days: Optional[int] = Field(None, ge=1, le=365)


class ConsumeIn(BaseModel):
    qty: float = Field(..., gt=0)
    project_id: Optional[int] = None
    reason: Optional[str] = None


class ShipToClientIn(BaseModel):
    reference: str = Field(..., min_length=1, max_length=120)
    expected_return_date: Optional[date] = None
    reason: Optional[str] = None


class ConfirmDeliveryIn(BaseModel):
    location_id: int
    reason: Optional[str] = None


class ReturnFromClientIn(BaseModel):
    location_id: int
    condition: ConditionEnum = ConditionEnum.WORKING
    reference: Optional[str] = Field(None, max_length=120)


class RepairOutIn(BaseModel):
    reference: str = Field(..., min_length=1, max_length=120)
    reason: Optional[str] = None


class RepairInIn(BaseModel):
    location_id: int
    condition: ConditionEnum = ConditionEnum.WORKING
    reference: Optional[str] = Field(None, max_length=120)


class ReasonIn(BaseModel):
    reason: str = Field(..., min_length=1)


class FoundIn(BaseModel):
    location_id: int
    reason: Optional[str] = None


class LostIn(BaseModel):
    reason: str = Field(..., min_length=1)
    recovery_amount: Optional[float] = Field(None, ge=0)


class WriteOffIn(BaseModel):
    reason: str = Field(..., min_length=1)
    recovery_amount: Optional[float] = Field(None, ge=0)


class AdjustIn(BaseModel):
    qty_delta: float
    reason: str = Field(..., min_length=1)
    reference: Optional[str] = Field(None, max_length=120)


# --- Requests -------------------------------------------------------------

class RequestIn(BaseModel):
    item_id: Optional[int] = None
    category_id: Optional[int] = None
    qty: float = Field(1.0, gt=0)
    project_id: Optional[int] = None
    purpose: str = Field(..., min_length=1)
    needed_by: Optional[date] = None


class RequestDecisionIn(BaseModel):
    approved: bool
    reason: Optional[str] = None


class RequestIssueIn(BaseModel):
    # Required when the request named a category rather than an item.
    item_id: Optional[int] = None
    qty: Optional[float] = Field(None, gt=0)
    expected_return_date: Optional[date] = None


class RequestOut(BaseModel):
    id: int
    requester_id: int
    requester_name: Optional[str] = None
    item_id: Optional[int] = None
    item_tag: Optional[str] = None
    item_name: Optional[str] = None
    category_id: Optional[int] = None
    category_name: Optional[str] = None
    qty: float
    project_id: Optional[int] = None
    purpose: str
    needed_by: Optional[date] = None
    status: str
    approver_id: Optional[int] = None
    approver_name: Optional[str] = None
    approved_at: Optional[datetime] = None
    rejection_reason: Optional[str] = None
    issued_movement_id: Optional[int] = None
    created_at: datetime

    class Config:
        from_attributes = True


# --- Audit ----------------------------------------------------------------

class AuditSessionIn(BaseModel):
    location_id: Optional[int] = None
    category_id: Optional[int] = None
    label: Optional[str] = Field(None, max_length=160)


class AuditScanIn(BaseModel):
    tag: str = Field(..., min_length=1)
    counted_qty: Optional[float] = Field(None, ge=0)
    note: Optional[str] = None


class AuditCloseIn(BaseModel):
    notes: Optional[str] = None


class AuditLineOut(BaseModel):
    id: int
    item_id: int
    item_tag: Optional[str] = None
    item_name: Optional[str] = None
    expected_qty: float
    counted_qty: Optional[float] = None
    variance: float
    variance_value: float
    outcome: Optional[str] = None
    note: Optional[str] = None
    scanned_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class AuditSessionOut(BaseModel):
    id: int
    label: Optional[str] = None
    scope_location_id: Optional[int] = None
    scope_location_code: Optional[str] = None
    scope_category_id: Optional[int] = None
    scope_category_name: Optional[str] = None
    status: str
    opened_by: int
    opened_by_name: Optional[str] = None
    opened_at: datetime
    closed_at: Optional[datetime] = None
    variance_count: int
    variance_value: float
    expected_count: int = 0
    scanned_count: int = 0
    notes: Optional[str] = None
    lines: List[AuditLineOut] = []

    class Config:
        from_attributes = True


# --- Reports --------------------------------------------------------------

class HolderRow(BaseModel):
    employee_id: int
    employee_name: str
    item_count: int
    overdue_count: int
    total_value: float = 0.0
    items: List[ItemOut] = []


class ProjectCostRow(BaseModel):
    project_id: Optional[int] = None
    project_code: Optional[str] = None
    project_name: Optional[str] = None
    material_cost: float
    line_count: int


class StockRow(BaseModel):
    item_id: int
    tag: str
    name: str
    qty: float
    min_qty: Optional[float] = None
    location_code: Optional[str] = None
