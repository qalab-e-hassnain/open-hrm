"""
Office fingerprint device: sync, mapping, and reconciliation against the portal.

The device corroborates presence. Portal check-in remains the source of hours,
because roughly half the device's records are a single scan — people badge in
and not out — and a duration derived from that would be wrong more often than
right.
"""
from datetime import date, datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from app.api.deps import require_admin_or_hr
from app.database import get_db
from app.models.biometric import BiometricIdentity, BiometricScan
from app.models.user import User
from app.utils import audit
from app.utils.biometric import DEVICE_UTC_OFFSET, reconcile_day, sync_month

router = APIRouter()


class IdentityOut(BaseModel):
    id: int
    device_name: str
    employee_id: Optional[int] = None
    employee_name: Optional[str] = None
    ignored: bool
    note: Optional[str] = None
    first_seen: Optional[date] = None
    last_seen: Optional[date] = None
    scan_count: int
    is_resolved: bool
    # Scans still attached to nobody. An ignored name keeps collecting these,
    # and they are the punches that never reach anyone's attendance — which is
    # the only way to tell a name that was ignored on purpose from one that was
    # ignored by mistake.
    unattached_scans: int = 0


class IdentityUpdate(BaseModel):
    """Map a device name to somebody, or mark it deliberately ignored."""
    employee_id: Optional[int] = None
    ignored: Optional[bool] = None
    note: Optional[str] = None


class ScanOut(BaseModel):
    device_name: str
    employee_id: Optional[int] = None
    employee_name: Optional[str] = None
    scan_date: date
    first_scan_local: str
    last_scan_local: str
    single_scan: bool


@router.post("/sync")
def run_sync(month: Optional[str] = None, request: Request = None,
             db: Session = Depends(get_db),
             current_user: User = Depends(require_admin_or_hr)):
    """
    Pull a month of badge records. Defaults to the current month.

    Safe to call repeatedly — records are upserted on (device name, date), so
    polling does not multiply them.
    """
    result = sync_month(db, month)
    if not result.get("ok"):
        # The device being unreachable is an operational fact, not a server
        # fault: say so plainly rather than returning a 500.
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY,
                            detail=f"Could not reach the attendance device: {result.get('error')}")
    audit.record(db, "biometric.synced", actor=current_user, request=request,
                 detail=result)
    return result


@router.get("/reconcile")
def reconcile(on: Optional[date] = None, db: Session = Depends(get_db),
              current_user: User = Depends(require_admin_or_hr)):
    """
    Where the device and the portal disagree, for one day.

    Only disagreements are returned — a day where everybody badged and checked
    in produces an empty list, which is the answer HR wants most days.
    """
    day = on or datetime.utcnow().date()
    issues = reconcile_day(db, day)
    return {"date": day, "issue_count": len(issues), "issues": issues}


@router.get("/identities", response_model=List[IdentityOut])
def list_identities(unresolved_only: bool = False,
                    state: str = Query("all", pattern="^(all|undecided|ignored|mapped)$"),
                    db: Session = Depends(get_db),
                    current_user: User = Depends(require_admin_or_hr)):
    """
    Every enrolment name the device has reported.

    `state` picks which ones:

      undecided  nobody has said what this name is — a newly enrolled joiner
      ignored    somebody said to disregard it
      mapped     pointed at an employee
      all        everything

    The distinction matters more than it looks. "Resolved" counts an ignored
    name as decided, so a screen that only offers undecided names reported
    that every name was accounted for while eleven names belonging to current
    employees sat ignored, quietly throwing away their punches. Ignoring a
    name is a decision somebody should be able to find again and change.

    `unresolved_only` is the old spelling of state=undecided, kept so existing
    callers keep working.
    """
    if unresolved_only and state == "all":
        state = "undecided"

    rows = (db.query(BiometricIdentity).options(joinedload(BiometricIdentity.employee))
            .order_by(BiometricIdentity.last_seen.desc().nullslast()).all())

    # How many of each name's scans still belong to nobody, in one query.
    orphans = dict(
        db.query(BiometricScan.device_name, func.count(BiometricScan.id))
        .filter(BiometricScan.employee_id.is_(None))
        .group_by(BiometricScan.device_name).all()
    )

    out = []
    for r in rows:
        if state == "undecided" and r.is_resolved:
            continue
        if state == "ignored" and not r.ignored:
            continue
        if state == "mapped" and r.employee_id is None:
            continue
        out.append(IdentityOut(
            id=r.id, device_name=r.device_name, employee_id=r.employee_id,
            employee_name=r.employee.full_name if r.employee else None,
            ignored=r.ignored, note=r.note, first_seen=r.first_seen,
            last_seen=r.last_seen, scan_count=r.scan_count, is_resolved=r.is_resolved,
            unattached_scans=int(orphans.get(r.device_name, 0))))
    return out


@router.put("/identities/{identity_id}", response_model=IdentityOut)
def update_identity(identity_id: int, payload: IdentityUpdate, request: Request,
                    db: Session = Depends(get_db),
                    current_user: User = Depends(require_admin_or_hr)):
    """
    Point a device name at an employee, or set it aside.

    Existing scans for that name are re-pointed too, so mapping a name that has
    been reporting for months brings its history with it rather than only
    applying from today — but never further back than the day they joined.

    A device enrolment outlives the person it was made for. `saad` reported
    from October 2025 to April 2026, went quiet for six months, and came back
    when a new Mian Saad Karim was enrolled under the same name; mapping it
    handed him thirty-nine days of somebody else's attendance from before he
    worked here. Scans older than a person's start date are left attached to
    nobody, which is the honest answer, because nobody knows whose they are.
    """
    ident = db.query(BiometricIdentity).filter(BiometricIdentity.id == identity_id).first()
    if not ident:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Identity not found")
    data = payload.model_dump(exclude_unset=True)
    if "employee_id" in data and data["employee_id"] is not None:
        if not db.query(User).filter(User.id == data["employee_id"]).first():
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Employee not found")
        ident.ignored = False
    for k, v in data.items():
        setattr(ident, k, v)
    scans = db.query(BiometricScan).filter(
        BiometricScan.device_name == ident.device_name)
    employee = (db.query(User).filter(User.id == ident.employee_id).first()
                if ident.employee_id else None)
    if employee and employee.date_of_joining:
        # Their own history only. Anything older stays unattached rather than
        # being credited to them — see the note above.
        scans = scans.filter(
            BiometricScan.scan_date >= employee.date_of_joining.date())
        db.query(BiometricScan).filter(
            BiometricScan.device_name == ident.device_name,
            BiometricScan.scan_date < employee.date_of_joining.date()).update(
                {"employee_id": None})
    scans.update({"employee_id": ident.employee_id})
    db.commit(); db.refresh(ident)
    audit.record(db, "biometric.identity_mapped", actor=current_user, request=request,
                 target_type="biometric_identity", target_id=ident.id,
                 detail={"device_name": ident.device_name,
                         "employee_id": ident.employee_id, "ignored": ident.ignored})
    return IdentityOut(
        id=ident.id, device_name=ident.device_name, employee_id=ident.employee_id,
        employee_name=ident.employee.full_name if ident.employee else None,
        ignored=ident.ignored, note=ident.note, first_seen=ident.first_seen,
        last_seen=ident.last_seen, scan_count=ident.scan_count,
        is_resolved=ident.is_resolved)


@router.get("/scans", response_model=List[ScanOut])
def list_scans(on: Optional[date] = None, employee_id: Optional[int] = None,
               db: Session = Depends(get_db),
               current_user: User = Depends(require_admin_or_hr)):
    """Badge records, with times converted back to local time for display."""
    q = db.query(BiometricScan).options(joinedload(BiometricScan.employee))
    if on:
        q = q.filter(BiometricScan.scan_date == on)
    if employee_id:
        q = q.filter(BiometricScan.employee_id == employee_id)
    rows = q.order_by(BiometricScan.scan_date.desc(), BiometricScan.first_scan).all()
    return [ScanOut(
        device_name=s.device_name, employee_id=s.employee_id,
        employee_name=s.employee.full_name if s.employee else None,
        scan_date=s.scan_date,
        first_scan_local=(s.first_scan + DEVICE_UTC_OFFSET).strftime("%H:%M:%S"),
        last_scan_local=(s.last_scan + DEVICE_UTC_OFFSET).strftime("%H:%M:%S"),
        single_scan=s.single_scan) for s in rows]
