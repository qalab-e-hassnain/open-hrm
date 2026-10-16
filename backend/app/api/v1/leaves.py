from fastapi import Request, APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy import func, or_
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import text
from typing import List, Optional
from app.database import get_db
from app.utils import audit
from app.utils.leave_year import open_balance
from app.schemas.leave import LeaveCreate, LeaveUpdate, LeaveResponse, LeaveApproval
from app.models.leave import Leave, LeaveStatusEnum, LeaveTypeEnum
from app.models.user import User, RoleEnum, GenderEnum
from app.api.deps import get_current_user, require_admin_or_hr, require_manager, is_privileged
from app.utils.notifications import create_leave_notification, notify_admins
from app.utils.leave_policy import parental_leave_for
from app.utils.email import send_leave_status_email
from app.models.notification import RelatedTypeEnum
from datetime import datetime

router = APIRouter()


# --- Revised leave policy (effective 01 January 2026) -----------------------

def _team_keys(db: Session, user) -> set:
    """
    The team(s) a person belongs to, identified by the manager at their head.

    A manager belongs to two: the group of their own reports, and the peer
    group under their own manager. Modelling membership as a single group
    cannot be made symmetric — their reports would count them as a teammate
    while they counted their peers instead — so membership is a set, and two
    people share a team when these sets intersect.
    """
    from app.models.user import User as _User

    keys = set()
    if user.manager_id:
        keys.add(user.manager_id)
    manages = db.query(_User).filter(
        _User.manager_id == user.id, _User.is_active == True,  # noqa: E712
    ).count()
    if manages:
        keys.add(user.id)
    return keys


def _team_of(db: Session, user_id: int) -> list:
    """
    Everyone sharing a team with this person, for the "one member off per day"
    rule. The `teams` table is unpopulated, so the reporting line is the only
    grouping available.
    """
    from app.models.user import User as _User

    me = db.query(_User).filter(_User.id == user_id).first()
    if not me:
        return []
    keys = _team_keys(db, me)
    if not keys:
        return []

    candidates = db.query(_User).filter(
        _User.is_active == True,  # noqa: E712
        or_(_User.manager_id.in_(keys), _User.id.in_(keys)),
    ).all()
    return [
        c for c in candidates
        if c.id != user_id and (_team_keys(db, c) & keys)
    ]


def _team_clash(db: Session, employee_id: int, start, end, exclude_leave_id=None) -> list:
    """Teammates already booked off across any part of the same window."""
    mates = {m.id: m.full_name for m in _team_of(db, employee_id)}
    if not mates:
        return []
    q = db.query(Leave).filter(
        Leave.employee_id.in_(list(mates)),
        Leave.start_date <= end,
        Leave.end_date >= start,
    )
    if exclude_leave_id:
        q = q.filter(Leave.id != exclude_leave_id)
    clashes = []
    for other in q.all():
        if _status_value(other) in ("approved", "pending", "manager_approved"):
            clashes.append(
                f"{mates[other.employee_id]} ({_status_value(other)} "
                f"{other.start_date:%d %b} – {other.end_date:%d %b})"
            )
    return clashes


def policy_warnings_for(db: Session, leave: Leave) -> list:
    """
    Advisory policy notes recomputed on read rather than stored on the row.

    Both of these can change after the request was filed — a certificate gets
    uploaded, a colleague cancels their leave — so a snapshot taken at request
    time would still be showing the approver a stale reason to hesitate.
    """
    notes = []
    if _status_value(leave) not in ("pending", "manager_approved"):
        return notes

    if _lt_value(leave) == "sick" and not leave.medical_certificate_url:
        notes.append(
            "No medical documentation attached. Policy limits sick leave to "
            "hospital admission or a doctor's prescription; without it this "
            "should be adjusted against personal or annual leave."
        )

    clashes = _team_clash(db, leave.employee_id, leave.start_date, leave.end_date, leave.id)
    if clashes:
        notes.append(
            "Only one team member may be on leave per day. Already booked: "
            + "; ".join(clashes) + "."
        )
    return notes


def enrich_leave_response(leave: Leave, warnings: Optional[list[str]] = None) -> LeaveResponse:
    """Helper function to enrich leave response with employee info, approver info, and warnings"""
    import logging
    logger = logging.getLogger(__name__)
    
    try:
        # Handle leave_type enum - Pydantic needs the enum object or a valid string value
        leave_type_val = leave.leave_type
        if isinstance(leave.leave_type, LeaveTypeEnum):
            # Already an enum, use it directly
            leave_type_val = leave.leave_type
        elif hasattr(leave.leave_type, 'value'):
            # It's an enum object, use it directly (Pydantic will handle conversion)
            leave_type_val = leave.leave_type
        elif isinstance(leave.leave_type, str):
            # It's a string, convert to enum
            try:
                leave_type_val = LeaveTypeEnum(leave.leave_type.lower())
            except ValueError:
                # If enum conversion fails, try to find by value
                leave_type_lower = leave.leave_type.lower()
                for enum_member in LeaveTypeEnum:
                    if enum_member.value == leave_type_lower:
                        leave_type_val = enum_member
                        break
                else:
                    # Last resort: use string and let Pydantic try
                    leave_type_val = leave.leave_type
        else:
            # Try to convert string representation to enum
            leave_type_str = str(leave.leave_type)
            try:
                leave_type_val = LeaveTypeEnum(leave_type_str.lower())
            except ValueError:
                leave_type_val = leave_type_str.lower()
        
        # Handle status enum - Pydantic needs the enum object or a valid string value
        status_val = leave.status
        if isinstance(leave.status, LeaveStatusEnum):
            # Already an enum, use it directly
            status_val = leave.status
        elif hasattr(leave.status, 'value'):
            # It's an enum object, use it directly (Pydantic will handle conversion)
            status_val = leave.status
        elif isinstance(leave.status, str):
            # It's a string, convert to enum
            try:
                status_val = LeaveStatusEnum(leave.status.lower())
            except ValueError:
                # If enum conversion fails, try to find by value
                status_lower = leave.status.lower()
                for enum_member in LeaveStatusEnum:
                    if enum_member.value == status_lower:
                        status_val = enum_member
                        break
                else:
                    # Last resort: use string and let Pydantic try
                    status_val = leave.status
        else:
            # Try to extract from enum object representation
            status_str = str(leave.status)
            if "LeaveStatusEnum." in status_str:
                enum_name = status_str.split(".")[-1].strip()
                try:
                    status_val = getattr(LeaveStatusEnum, enum_name)
                except AttributeError:
                    # Fallback to string value
                    try:
                        status_val = LeaveStatusEnum(status_str.lower())
                    except ValueError:
                        status_val = status_str.lower()
            else:
                try:
                    status_val = LeaveStatusEnum(status_str.lower())
                except ValueError:
                    status_val = status_str.lower()
        
        logger.debug(f"Enriching leave {leave.id}: leave_type={leave_type_val}, status={status_val} (type: {type(status_val)})")
        
        # Convert leave to dict, handling enum status properly
        # Pydantic will convert enum objects to their values automatically
        leave_dict = {
            "id": leave.id,
            "employee_id": leave.employee_id,
            "leave_type": leave_type_val,
            "start_date": leave.start_date,
            "end_date": leave.end_date,
            "reason": leave.reason,
            "status": status_val,
            "total_days": leave.total_days,
            "medical_certificate_url": leave.medical_certificate_url,
            "medical_certificate_required": leave.medical_certificate_required,
            "manager_approved_by": leave.manager_approved_by,
            "manager_approved_at": leave.manager_approved_at,
            "approved_by": leave.approved_by,
            "approved_at": leave.approved_at,
            "rejection_reason": leave.rejection_reason,
            "created_at": leave.created_at,
            "updated_at": leave.updated_at,
            "employee_name": leave.employee.full_name if leave.employee else None,
            "employee_email": leave.employee.email if leave.employee else None,
            "manager_approver_name": leave.manager_approver.full_name if leave.manager_approver else None,
            "approver_name": leave.approver.full_name if leave.approver else None,
            "warnings": warnings if warnings else None,
            "acting_admin_id": leave.acting_admin_id,
            "acting_admin_name": leave.acting_admin.full_name if leave.acting_admin else None,
        }
        logger.debug(f"Created leave_dict for leave {leave.id}, validating with LeaveResponse")
        return LeaveResponse(**leave_dict)
    except Exception as e:
        logger.error(f"Error in enrich_leave_response for leave {leave.id}: {e}")
        logger.error(f"Leave status type: {type(leave.status)}, value: {leave.status}")
        logger.error(f"Leave leave_type type: {type(leave.leave_type)}, value: {leave.leave_type}")
        import traceback
        logger.error(traceback.format_exc())
        # Fallback: try using model_validate
        try:
            logger.info("Trying fallback with model_validate")
            leave_dict = {
                **LeaveResponse.model_validate(leave).model_dump(),
                "employee_name": leave.employee.full_name if leave.employee else None,
                "employee_email": leave.employee.email if leave.employee else None,
                "manager_approver_name": leave.manager_approver.full_name if leave.manager_approver else None,
                "approver_name": leave.approver.full_name if leave.approver else None,
                "warnings": warnings if warnings else None,
                "acting_admin_name": leave.acting_admin.full_name if leave.acting_admin else None,
            }
            return LeaveResponse(**leave_dict)
        except Exception as e2:
            logger.error(f"Fallback also failed: {e2}")
            import traceback
            logger.error(traceback.format_exc())
            raise


@router.get("/", response_model=List[LeaveResponse])
def list_leaves(
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """List leaves - Employees see their own, Managers see subordinates', Admin sees all"""
    try:
        query = db.query(Leave).options(joinedload(Leave.employee), joinedload(Leave.approver), joinedload(Leave.manager_approver))
        
        if is_privileged(current_user):
            leaves = query.offset(skip).limit(limit).all()
        elif current_user.role == RoleEnum.MANAGER:
            # Get all subordinates
            subordinate_ids = [s.id for s in current_user.subordinates] + [current_user.id]
            leaves = query.filter(Leave.employee_id.in_(subordinate_ids)).offset(skip).limit(limit).all()
        else:
            leaves = query.filter(Leave.employee_id == current_user.id).offset(skip).limit(limit).all()
        
        # Convert to response models with employee info
        return [enrich_leave_response(leave) for leave in leaves]
    except Exception as e:
        error_msg = str(e).lower()
        if "manager_approved" in error_msg or ("column" in error_msg and "does not exist" in error_msg):
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Database schema is out of sync. The leaves table is missing required columns. Please run: python fix_database.py"
            )
        raise


@router.get("/history", response_model=List[LeaveResponse])
def get_leave_history(
    employee_id: Optional[int] = None,
    exclude_self: bool = False,
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Get leave history - Employees see their own, Managers see subordinates', Admin sees all.

    exclude_self omits the caller's own requests, so a manager or admin reviewing
    the team's history is not looking at their own leave mixed in with it. Their
    own requests remain visible with exclude_self off. Applied after the role
    scoping below, so it can only ever narrow the result.
    """
    try:
        query = db.query(Leave).options(joinedload(Leave.employee), joinedload(Leave.approver), joinedload(Leave.manager_approver))
        if exclude_self:
            query = query.filter(Leave.employee_id != current_user.id)

        if is_privileged(current_user):
            # Admin can see all or filter by specific employee
            if employee_id:
                leaves = query.filter(Leave.employee_id == employee_id).offset(skip).limit(limit).all()
            else:
                leaves = query.offset(skip).limit(limit).all()
        elif current_user.role == RoleEnum.MANAGER:
            # Managers can see their own and subordinates' history
            subordinate_ids = [s.id for s in current_user.subordinates] + [current_user.id]
            if employee_id:
                # Verify the employee_id is a subordinate or themselves
                if employee_id not in subordinate_ids:
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail="You can only view leave history of your subordinates or yourself"
                    )
                leaves = query.filter(Leave.employee_id == employee_id).offset(skip).limit(limit).all()
            else:
                leaves = query.filter(Leave.employee_id.in_(subordinate_ids)).offset(skip).limit(limit).all()
        else:
            # Employees can only see their own history
            if employee_id and employee_id != current_user.id:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="You can only view your own leave history"
                )
            leaves = query.filter(Leave.employee_id == current_user.id).offset(skip).limit(limit).all()

        # Enrich and return leaves with proper error handling
        enriched_leaves = []
        for leave in leaves:
            try:
                enriched_leaves.append(enrich_leave_response(leave))
            except Exception as enrich_err:
                import logging
                logger = logging.getLogger(__name__)
                logger.error(f"Error enriching leave {leave.id}: {enrich_err}")
                import traceback
                logger.error(traceback.format_exc())
                # Skip this leave and continue with others
                continue
        
        return enriched_leaves
    except HTTPException:
        # Re-raise HTTP exceptions (like 403)
        raise
    except Exception as e:
        import logging
        import traceback
        logger = logging.getLogger(__name__)
        error_msg = str(e).lower()
        logger.error(f"Error in get_leave_history: {e}")
        logger.error(traceback.format_exc())
        
        if "manager_approved" in error_msg or ("column" in error_msg and "does not exist" in error_msg):
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Database schema is out of sync. The leaves table is missing required columns. Please run: python fix_database.py"
            )
        if "comments" in error_msg and "does not exist" in error_msg:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Database schema is out of sync. The leaves table is missing the 'comments' column. Please run: ALTER TABLE leaves ADD COLUMN IF NOT EXISTS comments TEXT;"
            )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error retrieving leave history: {str(e)[:200]}"
        )


@router.get("/pending", response_model=List[LeaveResponse])
def list_pending_leaves(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_manager)
):
    """List pending leave requests (Manager/Admin only).
    - Admin/HR see every PENDING request, theirs to approve.
    - Regular managers see their subordinates' PENDING requests read-only —
      they can view but (see approve_or_reject_leave) can no longer act on them."""
    import traceback
    import logging
    logger = logging.getLogger(__name__)
    
    try:
        query = db.query(Leave).options(joinedload(Leave.employee),
                                        joinedload(Leave.approver),
                                        joinedload(Leave.manager_approver))

        # Filtered in SQL, case-insensitively. `leaves.status` is a varchar,
        # not a database enum, and it has held a mix of casings — which is why
        # this used to load every leave in the company and sift them in Python,
        # logging each one it discarded. lower() answers the same question
        # without reading forty-seven rows to return one.
        wanted = (["pending", "manager_approved"] if is_privileged(current_user)
                  else ["pending"])
        query = query.filter(func.lower(Leave.status).in_(wanted))

        if not is_privileged(current_user):
            # Managers see their subordinates' requests, never their own.
            subordinate_ids = [s.id for s in current_user.subordinates]
            if not subordinate_ids:
                return []
            query = query.filter(Leave.employee_id.in_(subordinate_ids))

        leaves = query.all()

        enriched_leaves = []
        for i, leave in enumerate(leaves):
            try:
                # The approval queue is where these matter most — an approver
                # working from the list should see a cover clash or a missing
                # medical certificate without opening each request first.
                enriched = enrich_leave_response(leave, policy_warnings_for(db, leave) or None)
                enriched_leaves.append(enriched)
            except Exception as enrich_err:
                logger.error(f"Error enriching leave {leave.id}: {enrich_err}")
                logger.error(traceback.format_exc())
                raise
        
        return enriched_leaves
    except HTTPException:
        # Re-raise HTTP exceptions as-is
        raise
    except Exception as e:
        error_msg = str(e).lower()
        error_type = type(e).__name__
        full_error = f"{error_type}: {str(e)}"
        logger.error(f"Error in list_pending_leaves: {full_error}")
        logger.error(traceback.format_exc())
        
        # Check for specific database schema errors
        if "column" in error_msg and "does not exist" in error_msg:
            if "manager_approved" in error_msg:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Database schema is out of sync. The leaves table is missing 'manager_approved_by' or 'manager_approved_at' columns. Please run: python fix_database.py"
                )
            else:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=f"Database schema error: Missing column. Please run: python fix_database.py. Error: {str(e)}"
                )
        # Check for enum value errors
        elif "invalid input value for enum" in error_msg or ("leavestatusenum" in error_msg and "manager_approved" in error_msg):
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Database enum is missing 'manager_approved' value. Please run: python fix_database.py"
            )
        # Re-raise with full error details for debugging
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error retrieving pending leaves: {full_error}"
        )


def _build_leave_balance(db: Session, employee: User) -> dict:
    """Build the leave balance payload for an employee, per company leave policy."""
    from app.models.leave_balance import LeaveBalance
    from app.utils.leave_policy import (
        LeavePolicyConfig, calculate_accrued_annual_leave,
        calculate_accrued_personal_leave, eligible_leave_types,
        months_served_in_year,
    )

    current_year = datetime.utcnow().year
    current_month = datetime.utcnow().month

    # Get or create leave balance
    # Opens the year on first touch, carrying unused annual leave forward
    # within the policy cap.
    leave_balance = open_balance(db, employee.id, current_year)

    # Calculate accrued leaves
    accrued_annual = calculate_accrued_annual_leave(employee.date_of_joining)
    accrued_personal = calculate_accrued_personal_leave(current_month, current_year)

    # The working behind the annual figure. Shown because "you have 10 days"
    # invites the question "why ten", and an employee who cannot answer that
    # from their own screen asks HR instead.
    from app.utils.leave_accrual import (personal_leave_remaining_in,
                                         personal_leave_used_in)
    personal_used_this_month = personal_leave_used_in(
        db, employee.id, current_month, current_year)
    personal_remaining_this_month = personal_leave_remaining_in(
        db, employee.id, current_month, current_year)

    months = months_served_in_year(employee.date_of_joining, current_year)
    per_month = LeavePolicyConfig.ANNUAL_LEAVE_PER_MONTH
    exact = round(months * per_month, 2)
    joined = employee.date_of_joining
    joined_this_year = bool(joined and joined.year == current_year)
    annual_accrual = {
        "months_served": months,
        "counted_from": (joined.date().isoformat() if joined_this_year
                         else f"{current_year}-01-01"),
        "rate_per_month": round(per_month, 2),
        "rate_per_year": LeavePolicyConfig.ANNUAL_LEAVE_PER_YEAR,
        "exact": exact,
        "rounded_up_to": accrued_annual,
        "resets_on": f"{current_year + 1}-01-01",
        "explanation": (
            f"{months} whole month{'s' if months != 1 else ''} served in "
            f"{current_year}"
            + (f" since joining on {joined.strftime('%d %B')}" if joined_this_year
               else " so far")
            + f", at {round(per_month, 2)} days a month, is {exact:g} days"
            + (f" — rounded up to {accrued_annual:g}." if accrued_annual != exact
               else ".")
            + " A month counts once it has ended. Nothing carries into next year."),
    }

    return {
        "employee_id": employee.id,
        "employee_name": employee.full_name,
        "year": current_year,
        "probation_status": "In Probation" if is_in_probation_for(employee) else "Confirmed",
        # Which types this person may request — maternity/paternity appear only
        # for a married employee of the matching gender.
        "eligible_leave_types": eligible_leave_types(employee),
        "marital_status": employee.marital_status,
        "leave_balances": {
            "annual": {
                "total": leave_balance.annual_total,
                "accrued": accrued_annual,
                "used": leave_balance.annual_used,
                "carryover": leave_balance.annual_carryover,
                "remaining": leave_balance.annual_remaining,
                "max_carryover": LeavePolicyConfig.ANNUAL_LEAVE_MAX_CARRYOVER,
                "accrual": annual_accrual,
            },
            "sick": {
                "total": leave_balance.sick_total,
                "used": leave_balance.sick_used,
                "remaining": leave_balance.sick_remaining,
                # Documentation is expected from the first sick day under the
                # revised policy; the threshold is the policy's, not a literal.
                "medical_cert_required_after":
                    LeavePolicyConfig.SICK_LEAVE_MEDICAL_CERT_THRESHOLD,
            },
            # A monthly allowance, not an annual pool. Unused personal leave
            # expires with the month, so "total" is this month's day and
            # "remaining" is what is left of it — the year-to-date figures are
            # kept alongside for reporting, not for spending.
            "personal": {
                "total": float(LeavePolicyConfig.PERSONAL_LEAVE_PER_MONTH),
                "used": personal_used_this_month,
                "remaining": personal_remaining_this_month,
                "carryover": 0.0,
                "accrual_rate": "1 day per month, use it or lose it",
                "period": f"{current_year}-{current_month:02d}",
                "used_this_year": leave_balance.personal_used,
                "explanation": (
                    f"One day of personal leave a month. "
                    f"{personal_used_this_month:g} used this month, "
                    f"{personal_remaining_this_month:g} left. It does not carry "
                    "into next month — an unused day is lost at month end."),
            },
            "maternity": {
                "total": leave_balance.maternity_total,
                "used": leave_balance.maternity_used,
                "remaining": leave_balance.maternity_remaining,
                "duration": "12 weeks (60 working days)"
            },
            "paternity": {
                "total": leave_balance.paternity_total,
                "used": leave_balance.paternity_used,
                "remaining": leave_balance.paternity_remaining,
                "duration": "2 weeks (10 working days)"
            },
            "bereavement": {
                "total": leave_balance.bereavement_total,
                "used": leave_balance.bereavement_used,
                "remaining": leave_balance.bereavement_remaining,
                "max_days": LeavePolicyConfig.BEREAVEMENT_LEAVE_MAX,
            }
        },
        "policy_info": {
            "unapproved_leave_penalty":
                f"Rs {LeavePolicyConfig.UNAPPROVED_LEAVE_DEDUCTION:,} per day",
            "planned_leave_notice":
                f"{LeavePolicyConfig.PLANNED_LEAVE_NOTICE_DAYS} days in advance",
            "personal_leave_notice":
                f"{LeavePolicyConfig.PERSONAL_LEAVE_NOTICE_HOURS} hours in advance",
            "weekend_policy": "Saturday and Sunday are off"
        }
    }


# NOTE: both /balance routes must be registered before "/{leave_id}" or that
# route captures the literal path segment "balance" and fails int validation.
@router.get("/balance", response_model=dict)
def get_leave_balance(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Get leave balance for the current user, per the leave policy"""
    return _build_leave_balance(db, current_user)


@router.get("/balance/{employee_id}", response_model=dict)
def get_employee_leave_balance(
    employee_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Get an employee's leave balance (admin, the employee's manager, or self)"""
    employee = db.query(User).filter(User.id == employee_id).first()
    if not employee:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Employee not found"
        )

    is_self = employee.id == current_user.id
    is_their_manager = current_user.role == RoleEnum.MANAGER and employee.manager_id == current_user.id
    if not (is_privileged(current_user) or is_their_manager or is_self):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not enough permissions to view this employee's leave balance"
        )

    return _build_leave_balance(db, employee)


@router.get("/{leave_id}", response_model=LeaveResponse)
def get_leave(
    leave_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Get leave by ID"""
    leave = db.query(Leave).options(joinedload(Leave.employee), joinedload(Leave.approver), joinedload(Leave.manager_approver)).filter(Leave.id == leave_id).first()
    
    if not leave:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Leave not found"
        )
    
    # Check permissions
    if (is_privileged(current_user) or 
        leave.employee_id == current_user.id or
        (current_user.role == RoleEnum.MANAGER and leave.employee.manager_id == current_user.id)):
        return enrich_leave_response(leave, policy_warnings_for(db, leave) or None)
    else:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not enough permissions"
        )


def _lt_value(leave) -> str:
    """Robustly get a leave's type as a lowercase string (handles enum/str)."""
    lt = leave.leave_type
    return lt.value if hasattr(lt, "value") else str(lt).split(".")[-1].lower()


def _status_value(leave) -> str:
    """Robustly get a leave's status as a lowercase string (handles enum/str)."""
    st = leave.status
    return st.value if hasattr(st, "value") else str(st).split(".")[-1].lower()


def _outstanding_committed_days(db: Session, employee_id: int, leave_type_value: str) -> float:
    """
    Days already requested for this leave type but NOT yet finalized
    (status PENDING or MANAGER_APPROVED). These are reserved against the
    allowance so an employee can't stack multiple requests that individually
    fit but together exceed their balance. (APPROVED days are already counted
    in *_used, so they're excluded here.)
    """
    leaves = db.query(Leave).filter(Leave.employee_id == employee_id).all()
    return sum(
        (l.total_days or 0)
        for l in leaves
        if _lt_value(l) == leave_type_value and _status_value(l) in ("pending", "manager_approved")
    )


# Maps a leave type to the LeaveBalance *_used attribute it draws down.
_USED_FIELD = {
    "annual": "annual_used",
    "sick": "sick_used",
    "casual": "personal_used",   # "casual" leave draws from the personal bucket
    "maternity": "maternity_used",
    "paternity": "paternity_used",
    "bereavement": "bereavement_used",
}


@router.post("/", response_model=LeaveResponse, status_code=status.HTTP_201_CREATED)
def create_leave_request(
    leave_data: LeaveCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Create a new leave request with policy validation
    Based on the leave policy (Oct 16, 2024)
    """
    from app.utils.leave_policy import (
        calculate_working_days, is_in_probation_for, 
        requires_medical_certificate, is_advance_notice_sufficient,
        validate_leave_balance, LeavePolicyConfig
    )
    from app.models.leave_balance import LeaveBalance
    
    # Validate dates (allow equal dates for single-day leaves)
    if leave_data.start_date > leave_data.end_date:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Start date must be before or equal to end date"
        )
    
    # Calculate working days
    working_days = calculate_working_days(leave_data.start_date, leave_data.end_date)
    
    if working_days <= 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Leave request must include at least one working day (Mon-Fri)"
        )

    # Refuse a request that overlaps one of this employee's own existing
    # requests still on the books (pending or approved — a rejected/cancelled
    # one is no longer live and doesn't block anything). ApplyLeave.tsx
    # already blocks this in the form, but that is client-side only and can
    # be bypassed by calling this endpoint directly, so it is enforced here
    # too — the form's check exists for a fast, friendly error; this one is
    # what actually prevents it.
    existing_leaves = db.query(Leave).filter(
        Leave.employee_id == current_user.id,
        Leave.start_date <= leave_data.end_date,
        Leave.end_date >= leave_data.start_date,
    ).all()
    for other in existing_leaves:
        if _status_value(other) in ("pending", "manager_approved", "approved"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"This request overlaps your existing {_lt_value(other).title()} leave "
                    f"request from {other.start_date:%d %b %Y} to {other.end_date:%d %b %Y}. "
                    "Cancel that request first, or wait for a decision on it."
                ),
            )

    # Collect warnings for frontend display
    warnings: list[str] = []

    # Parental leave is earned by circumstance: married employees only, and the
    # gender decides which of the two. Anyone else is refused with the reason,
    # so an employee whose record is simply out of date knows what to fix.
    if leave_data.leave_type in (LeaveTypeEnum.MATERNITY, LeaveTypeEnum.PATERNITY):
        allowed = parental_leave_for(current_user)
        if leave_data.leave_type.value != allowed:
            married = str(current_user.marital_status or "").lower() == "married"
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"{leave_data.leave_type.value.title()} leave is not applicable to your profile."
                    if married else
                    f"{leave_data.leave_type.value.title()} leave is available to married employees only. "
                    "Ask HR to update your marital status if this is wrong."
                ),
            )

    # Check probation period restrictions (not applicable to admins)
    # Note: Casual/Personal leave (1 day/month) is allowed during probation
    # Annual leave during probation is allowed but will be flagged for HR review
    # Probation no longer flags annual leave. Leave is accrued by the month now,
    # so somebody in their first weeks has one or two days and cannot take more
    # than they have earned — the balance already does what the warning was for,
    # and the warning only told them to go and ask HR about days they own.

    # Get or create leave balance for current year
    current_year = datetime.utcnow().year
    leave_balance = open_balance(db, current_user.id, current_year)
    
    # Validate leave balance (except for unpaid leave). Outstanding requests
    # (pending / manager-approved) are RESERVED against the allowance too, so an
    # employee can't stack several requests that individually fit but together
    # exceed their balance. If they need more, they must contact the admin.
    leave_type_value = (leave_data.leave_type.value
                        if hasattr(leave_data.leave_type, "value")
                        else str(leave_data.leave_type).lower())
    if leave_type_value != "unpaid":
        # Personal leave is spent against the month it falls in, so the check
        # uses that month rather than a year-wide pool — and because the helper
        # already counts outstanding requests, it needs no separate reservation.
        from app.utils.leave_accrual import personal_leave_remaining_in
        remaining = {
            "annual": leave_balance.annual_remaining,
            "sick": leave_balance.sick_remaining,
            "casual": personal_leave_remaining_in(
                db, current_user.id, leave_data.start_date.month,
                leave_data.start_date.year),
            "maternity": leave_balance.maternity_remaining,
            "paternity": leave_balance.paternity_remaining,
            "bereavement": leave_balance.bereavement_remaining,
        }.get(leave_type_value, 0)
        already_committed = (0.0 if leave_type_value == "casual"
                             else _outstanding_committed_days(db, current_user.id,
                                                              leave_type_value))
        available = remaining - already_committed
        if working_days > available:
            committed_note = (f" ({already_committed:g} day(s) already pending or approved)"
                              if already_committed else "")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Insufficient {leave_type_value} leave balance. You requested "
                    f"{working_days} day(s) but only {max(0.0, available):g} day(s) are "
                    f"available{committed_note}. To take leave beyond your allowance, "
                    f"please contact the admin."
                ),
            )
    
    # Check if medical certificate required
    medical_cert_required = requires_medical_certificate(leave_data.leave_type, working_days)
    if medical_cert_required and not leave_data.medical_certificate_url:
        warnings.append("Sick leave requires medical documentation (hospital admission or a doctor's prescription). Without it, this will be adjusted against your personal or annual leave.")
    
    # Advance notice. Personal ("casual") leave is on a 48-hour rule of its
    # own since the revised policy; the rest keep the two-week requirement.
    now = datetime.utcnow()
    if leave_data.leave_type == "casual":
        from app.utils.leave_policy import is_personal_notice_sufficient
        if not is_personal_notice_sufficient(now, leave_data.start_date):
            hours = (leave_data.start_date - now).total_seconds() / 3600
            warnings.append(
                f"Personal leave requires 48 hours notice; this is {max(0, hours):.0f} hours "
                "ahead. Personal leave is not automatic — same-day requests are considered "
                "only for genuine emergencies."
            )
    elif leave_data.leave_type in ["annual", "maternity", "paternity"]:
        if not is_advance_notice_sufficient(now, leave_data.start_date):
            days_until_leave = (leave_data.start_date - now).days
            warnings.append(f"Leave requested with less than 2 weeks advance notice ({days_until_leave} days). Company policy requires at least 14 days notice for planned leaves.")

    # One member per team per day — advisory, decided by the approver.
    _clashes = _team_clash(db, current_user.id, leave_data.start_date, leave_data.end_date)
    if _clashes:
        warnings.append(
            "Only one team member may be on leave per day. Already booked: "
            + "; ".join(_clashes) + "."
        )
    
    # Acting admin (who covers full admin authority while this leave is
    # approved and active) is an admin-only choice — everyone else gets 400
    # if they try to set it, rather than the field silently doing nothing.
    if leave_data.acting_admin_id is not None:
        if current_user.role != RoleEnum.ADMIN:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Only an administrator can designate an acting admin.",
            )
        acting_admin_user = db.query(User).filter(
            User.id == leave_data.acting_admin_id, User.is_active == True
        ).first()
        if not acting_admin_user:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="The selected acting admin could not be found or is inactive.",
            )

    # Create leave request
    leave = Leave(
        employee_id=current_user.id,
        leave_type=leave_data.leave_type,
        start_date=leave_data.start_date,
        end_date=leave_data.end_date,
        reason=leave_data.reason,
        comments=leave_data.comments,  # Add comments field
        status=LeaveStatusEnum.PENDING,
        total_days=working_days,
        medical_certificate_required=medical_cert_required,
        medical_certificate_url=leave_data.medical_certificate_url,
        acting_admin_id=leave_data.acting_admin_id if current_user.role == RoleEnum.ADMIN else None,
    )
    
    try:
        db.add(leave)
        db.commit()
        db.refresh(leave)
    except Exception as e:
        db.rollback()
        error_msg = str(e).lower()
        # Check for missing column errors
        if "manager_approved" in error_msg or ("column" in error_msg and "does not exist" in error_msg):
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Database schema is out of sync. The leaves table is missing required columns. Please run: python fix_database.py"
            )
        # Re-raise to let the global handler catch it
        raise

    leave_type = getattr(leave.leave_type, "value", leave.leave_type)
    notify_admins(
        db=db,
        title="New leave request",
        message=(
            f"{current_user.full_name} requested {working_days} working day(s) of "
            f"{leave_type} leave from {leave.start_date:%d %b %Y} to {leave.end_date:%d %b %Y}."
        ),
        related_id=leave.id,
        related_type=RelatedTypeEnum.LEAVE,
        exclude_user_id=current_user.id,
    )

    # Reload with employee, approver, and manager_approver relationships
    leave = db.query(Leave).options(joinedload(Leave.employee), joinedload(Leave.approver), joinedload(Leave.manager_approver)).filter(Leave.id == leave.id).first()
    return enrich_leave_response(leave, warnings if warnings else None)


@router.put("/{leave_id}", response_model=LeaveResponse)
def update_leave(
    leave_id: int,
    leave_data: LeaveUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Update leave request (only if pending)"""
    leave = db.query(Leave).filter(Leave.id == leave_id).first()
    
    if not leave:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Leave not found"
        )
    
    # Only the employee can update their own pending leave
    if leave.employee_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only update your own leave requests"
        )
    
    if leave.status != LeaveStatusEnum.PENDING:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Can only update pending leave requests"
        )
    
    # Update fields
    update_data = leave_data.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(leave, field, value)
    
    db.commit()
    db.refresh(leave)
    
    # Reload with employee, approver, and manager_approver relationships
    leave = db.query(Leave).options(joinedload(Leave.employee), joinedload(Leave.approver), joinedload(Leave.manager_approver)).filter(Leave.id == leave.id).first()
    return enrich_leave_response(leave)


@router.put("/{leave_id}/approve", response_model=LeaveResponse)
def approve_or_reject_leave(
    leave_id: int,
    approval_data: LeaveApproval,
    background_tasks: BackgroundTasks,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin_or_hr)
):
    """
    Single-stage approval: only an admin or an HR-flagged manager can approve or
    reject a leave request — regular managers have no approval role at all.
    A PENDING request goes straight to APPROVED or REJECTED, no intermediate
    manager sign-off. (MANAGER_APPROVED still exists on the status enum purely
    to read any historical rows from before this change; nothing sets it anymore.)
    """
    import logging
    logger = logging.getLogger(__name__)
    from app.models.leave_balance import LeaveBalance
    
    leave = db.query(Leave).options(joinedload(Leave.employee), joinedload(Leave.approver), joinedload(Leave.manager_approver)).filter(Leave.id == leave_id).first()
    
    if not leave:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Leave not found"
        )

    # Nobody decides their own leave, admin or not. The reimbursement and
    # advance endpoints already refuse this; leave did not, so the one role
    # that approves leave could approve its own. Somebody else with HR reach
    # decides it — and while an admin is away, whoever is covering for them
    # can (see is_acting_admin).
    if leave.employee_id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You cannot decide your own leave request.",
        )

    # Get current status as string for consistent comparison
    # CaseInsensitiveEnum returns enum objects, so we need to extract the value
    # Direct approach: try multiple methods to get the value
    current_status_str = None
    
    # Method 1: Direct value access (most common case)
    try:
        if hasattr(leave.status, 'value'):
            current_status_str = leave.status.value
    except:
        pass
    
    # Method 2: If it's a string, use it directly
    if current_status_str is None and isinstance(leave.status, str):
        current_status_str = leave.status.lower()
    
    # Method 3: Check if it's an enum member and get value
    if current_status_str is None:
        try:
            if isinstance(leave.status, LeaveStatusEnum):
                current_status_str = leave.status.value
        except:
            pass
    
    # Method 4: Extract from string representation
    if current_status_str is None:
        status_str = str(leave.status)
        # Map enum names to values
        enum_name_to_value = {
            "MANAGER_APPROVED": "manager_approved",
            "PENDING": "pending",
            "APPROVED": "approved",
            "REJECTED": "rejected",
            "CANCELLED": "cancelled"
        }
        for name, value in enum_name_to_value.items():
            if name in status_str:
                current_status_str = value
                break
    
    # Method 5: Last resort - use string as-is (lowercase)
    if current_status_str is None:
        current_status_str = str(leave.status).lower()
        # If it still contains enum info, try to extract
        if "LeaveStatusEnum." in current_status_str:
            parts = current_status_str.split(".")
            if len(parts) > 1:
                enum_name = parts[-1].strip()
                try:
                    current_status_str = getattr(LeaveStatusEnum, enum_name).value
                except:
                    current_status_str = enum_name_to_value.get(enum_name.upper(), current_status_str)
    
    # Nobody decides their own request — not even an admin. An admin's own leave
    # has to be approved by a different admin (HR), the same segregation of duties
    # already applied to managers below. Checked once here so it covers both the
    # rejection and approval paths.
    if leave.employee_id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "You cannot approve or reject your own leave request. "
                "Another admin has to review it."
            )
        )

    # Handle rejection. No role branching needed here beyond the dependency
    # itself — require_admin_or_hr already guarantees only an admin or HR
    # manager reached this point, and the self-rejection guard above already
    # covers "not your own request."
    if approval_data.status == LeaveStatusEnum.REJECTED:
        if current_status_str in ["approved", "cancelled"]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Cannot reject an already approved or cancelled leave. Current status: {current_status_str}"
            )

        leave.status = LeaveStatusEnum.REJECTED
        leave.rejection_reason = approval_data.rejection_reason
        # Update comments if provided in approval request
        if approval_data.comments:
            # Append to existing comments or set new
            if leave.comments:
                leave.comments = f"{leave.comments}\n\n[Approver Comment]: {approval_data.comments}"
            else:
                leave.comments = f"[Approver Comment]: {approval_data.comments}"
        db.commit()
        db.refresh(leave)
        
        # Create notification for employee
        try:
            approver_name = current_user.full_name
            create_leave_notification(
                db=db,
                user_id=leave.employee_id,
                leave_id=leave.id,
                status="rejected",
                approver_name=approver_name
            )
        except Exception as e:
            # Don't fail the request if notification creation fails
            print(f"Failed to create notification: {e}")

        if leave.employee and leave.employee.email:
            background_tasks.add_task(
                send_leave_status_email,
                employee_email=leave.employee.email,
                leave_type=_lt_value(leave).title(),
                start_date=leave.start_date.strftime("%d %b %Y"),
                end_date=leave.end_date.strftime("%d %b %Y"),
                status="rejected",
                rejection_reason=leave.rejection_reason,
            )

        audit.record(db, "leave.rejected", actor=current_user, request=request,
                     target_type="leave", target_id=leave.id,
                     detail={"employee_id": leave.employee_id,
                             "leave_type": _lt_value(leave),
                             "days": leave.total_days,
                             "reason": leave.rejection_reason})

        leave = db.query(Leave).options(joinedload(Leave.employee), joinedload(Leave.approver), joinedload(Leave.manager_approver)).filter(Leave.id == leave.id).first()
        return enrich_leave_response(leave)

    # Handle approval. Single stage: PENDING (or a historical MANAGER_APPROVED
    # row from before this change) goes straight to APPROVED. No role branch
    # needed — require_admin_or_hr already guarantees only an admin or HR
    # manager reached this point.
    if current_status_str not in ["pending", "manager_approved"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot approve leave with status: {current_status_str}. Only a pending leave can be approved."
        )

    # Check medical certificate requirement for sick leave
    if (leave.leave_type == "sick" and
        leave.medical_certificate_required and
        not leave.medical_certificate_url):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Medical certificate required for sick leave of {leave.total_days} days"
        )

    logger.info(f"Approving leave {leave.id}, changing status from {current_status_str} to APPROVED")
    leave.status = LeaveStatusEnum.APPROVED
    if approval_data.comments:
        if leave.comments:
            leave.comments = f"{leave.comments}\n\n[Admin Comment]: {approval_data.comments}"
        else:
            leave.comments = f"[Admin Comment]: {approval_data.comments}"
    leave.approved_by = current_user.id
    leave.approved_at = datetime.utcnow()

    # Deduct from leave balance only on final approval (APPROVED status)
    if leave.status == LeaveStatusEnum.APPROVED:
        lt = _lt_value(leave)
        used_field = _USED_FIELD.get(lt)  # None for unpaid
        if used_field:
            # The balance is drawn against the year the leave falls in, not
            # the year it happens to be approved in — a December request
            # signed off in January belongs to December. Opened on demand so
            # an approval can never silently skip the deduction because
            # nobody had touched that year yet.
            leave_balance = open_balance(db, leave.employee_id, leave.start_date.year)
            if leave_balance:
                # Personal leave is spent against the month it falls in, so the
                # approver is held to the same monthly day the request was
                # filed against — the year-wide pool would wave through a
                # second day in the same month. This request is excluded from
                # the count; it is the one being decided.
                from app.utils.leave_accrual import personal_leave_remaining_in
                remaining = {
                    "annual": leave_balance.annual_remaining,
                    "sick": leave_balance.sick_remaining,
                    "casual": personal_leave_remaining_in(
                        db, leave.employee_id, leave.start_date.month,
                        leave.start_date.year, exclude_leave_id=leave.id),
                    "maternity": leave_balance.maternity_remaining,
                    "paternity": leave_balance.paternity_remaining,
                    "bereavement": leave_balance.bereavement_remaining,
                }.get(lt, 0)
                # Guard: never let an approval push used past the allowance.
                if (leave.total_days or 0) > remaining:
                    db.rollback()
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=(
                            f"Approving this would exceed the employee's {lt} leave "
                            f"allowance (remaining {remaining:g} day(s), request is "
                            f"{leave.total_days} day(s)). Reduce the request or record the "
                            f"excess as unpaid leave."
                        ),
                    )
                setattr(leave_balance, used_field,
                        getattr(leave_balance, used_field) + (leave.total_days or 0))
    
    try:
        # Log status before commit
        logger.info(f"Before commit - Leave {leave.id} status: {leave.status} (type: {type(leave.status)})")
        
        # Ensure status is set as enum object before commit
        # CaseInsensitiveEnum will convert it to the correct string value
        if isinstance(leave.status, str):
            # If it's a string, convert to enum first
            try:
                leave.status = LeaveStatusEnum(leave.status.lower())
                logger.info(f"Converted string status to enum: {leave.status}")
            except ValueError:
                # If enum conversion fails, try to find by name
                status_upper = leave.status.upper()
                if hasattr(LeaveStatusEnum, status_upper):
                    leave.status = getattr(LeaveStatusEnum, status_upper)
                    logger.info(f"Converted status by name to enum: {leave.status}")
                else:
                    raise ValueError(f"Invalid status value: {leave.status}")
        
        logger.info(f"Committing leave {leave.id} with status: {leave.status} (value: {leave.status.value if hasattr(leave.status, 'value') else leave.status})")
        db.commit()
        db.refresh(leave)
        # Log the updated status after commit
        logger.info(f"After commit and refresh - Leave {leave.id} status: {leave.status} (type: {type(leave.status)})")
    except Exception as e:
        db.rollback()
        error_msg = str(e).lower()
        logger.error(f"❌ Database error during leave approval: {e}")
        logger.error(f"Leave status type: {type(leave.status)}, value: {leave.status}")
        
        # If enum value case mismatch (database has APPROVED but we're sending approved)
        if "invalid input value for enum" in error_msg:
            logger.warning(f"Enum value case mismatch detected. Database has uppercase, code sending lowercase. Fixing...")
            try:
                # Map lowercase Python enum values to uppercase database values
                status_value = leave.status.value if hasattr(leave.status, 'value') else str(leave.status).lower()
                status_upper_map = {
                    "approved": "APPROVED",
                    "pending": "PENDING",
                    "rejected": "REJECTED",
                    "cancelled": "CANCELLED",
                    "manager_approved": "manager_approved"  # This one is lowercase in DB
                }
                db_status_value = status_upper_map.get(status_value, status_value.upper())
                
                logger.info(f"Converting '{status_value}' to database case '{db_status_value}' and updating via raw SQL...")
                
                # Use raw SQL to update with correct case
                db.execute(text("""
                    UPDATE leaves 
                    SET status = :status, 
                        approved_by = :approved_by, 
                        approved_at = :approved_at,
                        updated_at = :updated_at
                    WHERE id = :leave_id
                """), {
                    "status": db_status_value,
                    "approved_by": leave.approved_by,
                    "approved_at": leave.approved_at,
                    "updated_at": datetime.utcnow(),
                    "leave_id": leave.id
                })
                db.commit()
                db.refresh(leave)
                logger.info(f"✅ Raw SQL update successful with correct case '{db_status_value}' - Leave {leave.id} status updated")
            except Exception as fix_err:
                logger.error(f"Could not fix enum case issue: {fix_err}")
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=f"Database enum case mismatch. Database has uppercase values (APPROVED, PENDING, etc.) but code is sending lowercase. Error: {str(e)[:200]}"
                )
        elif "invalid input value for enum" in error_msg:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Database enum error. The status value '{leave.status}' is not valid for the database enum. Please check the database enum values. Error: {str(e)[:200]}. Run: python check_database.py to verify enum values."
            )
        if "manager_approved" in error_msg:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Database enum error. The 'manager_approved' enum value might not be properly added. Error: {str(e)[:200]}. Please ensure you've restarted the server after running: python fix_database.py"
            )
        # Re-raise other errors to let global handler catch them
        raise
    
    # Create notification for employee — leave.status is always APPROVED here
    # (the approval branch above never leaves it as anything else).
    try:
        create_leave_notification(
            db=db,
            user_id=leave.employee_id,
            leave_id=leave.id,
            status="approved",
            approver_name=current_user.full_name,
        )
    except Exception as e:
        # Don't fail the request if notification creation fails
        print(f"Failed to create notification: {e}")

    if leave.employee and leave.employee.email:
        background_tasks.add_task(
            send_leave_status_email,
            employee_email=leave.employee.email,
            leave_type=_lt_value(leave).title(),
            start_date=leave.start_date.strftime("%d %b %Y"),
            end_date=leave.end_date.strftime("%d %b %Y"),
            status="approved",
        )

    audit.record(db, "leave.approved", actor=current_user, request=request,
                 target_type="leave", target_id=leave.id,
                 detail={"employee_id": leave.employee_id,
                         "leave_type": _lt_value(leave),
                         "days": leave.total_days,
                         "start": leave.start_date, "end": leave.end_date})

    # Reload with employee, approver, and manager_approver relationships
    leave = db.query(Leave).options(joinedload(Leave.employee), joinedload(Leave.approver), joinedload(Leave.manager_approver)).filter(Leave.id == leave.id).first()
    return enrich_leave_response(leave)


@router.delete("/{leave_id}/purge", status_code=status.HTTP_204_NO_CONTENT)
def purge_leave_record(
    leave_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin_or_hr),
):
    """
    Permanently remove a finished leave record from the history (Admin only).

    Unlike DELETE /{leave_id}, which cancels a request and keeps the row, this
    erases it — for clearing out history that has grown too long. Two guards
    keep that from corrupting anything downstream:

    * Only a finished record can go. A request still awaiting approval must be
      approved, rejected or cancelled first — deleting it would drop it out of
      the queue silently.
    * Approved UNPAID leave is refused. api/v1/payroll._unpaid_leave_days_in_month
      reads these rows to derive the approved unpaid-leave deduction, so removing one would
      change an employee's pay the next time that month is recomputed.

    Leave balances live in their own table and are not recalculated from these
    rows, so deleting a record does not hand days back.
    """
    leave = db.query(Leave).filter(Leave.id == leave_id).first()
    if not leave:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Leave not found")

    leave_status = _status_value(leave)
    if leave_status not in ("approved", "rejected", "cancelled"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Only a finished leave record can be deleted. Approve, reject or "
                "cancel this request first."
            ),
        )

    leave_type = leave.leave_type.value if hasattr(leave.leave_type, "value") else str(leave.leave_type).split(".")[-1].lower()
    if leave_status == "approved" and leave_type == "unpaid":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "This approved unpaid leave is used to calculate the unpaid-leave "
                "deduction on payroll. Deleting it would change the employee's pay "
                "for that month, so it has to stay on record."
            ),
        )

    # Capture the row before it is gone — this is the one operation where the
    # record itself cannot be consulted afterwards to see what was removed.
    erased = {
        "employee_id": leave.employee_id, "leave_type": leave_type,
        "status": leave_status, "days": leave.total_days,
        "start": leave.start_date, "end": leave.end_date, "reason": leave.reason,
    }
    db.delete(leave)
    db.commit()

    audit.record(db, "leave.purged", actor=current_user, request=request,
                 target_type="leave", target_id=leave_id, detail=erased)
    return None


@router.delete("/{leave_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_leave(
    leave_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Delete/Cancel leave request"""
    leave = db.query(Leave).filter(Leave.id == leave_id).first()
    
    if not leave:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Leave not found"
        )
    
    # Only employee can delete their own leave or admin
    if leave.employee_id != current_user.id and not is_privileged(current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not enough permissions"
        )
    
    # Once approved, the leave is committed — payroll and team coverage may
    # already be planned around it — so self-service withdrawal stops here.
    # Undoing an approved leave takes a deliberate HR/Admin action instead
    # (reversing the approval, or purging the record), not a quiet pull-back.
    current_status = _status_value(leave)
    if current_status == "approved":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This leave is already approved and cannot be cancelled. Contact HR/Admin.",
        )

    # Mark as cancelled instead of deleting
    leave.status = LeaveStatusEnum.CANCELLED
    db.commit()

    leave_type = getattr(leave.leave_type, "value", leave.leave_type)
    if current_user.id == leave.employee_id:
        # Self-withdrawal: whoever was going to review this (it was announced
        # to them on submission) needs to know it's off the table, not just
        # see it silently vanish from their queue.
        notify_admins(
            db=db,
            title="Leave request cancelled",
            message=(
                f"{current_user.full_name} cancelled their {leave_type} leave request "
                f"for {leave.start_date:%d %b %Y} to {leave.end_date:%d %b %Y}."
            ),
            related_id=leave.id,
            related_type=RelatedTypeEnum.LEAVE,
            exclude_user_id=current_user.id,
        )
    else:
        # Admin/HR cancelled it on the employee's behalf — they need to know
        # their request was pulled, same as any other status change.
        create_leave_notification(
            db=db,
            user_id=leave.employee_id,
            leave_id=leave.id,
            status="cancelled",
            approver_name=current_user.full_name,
        )

    return None


from app.utils.leave_policy import is_in_probation_for

