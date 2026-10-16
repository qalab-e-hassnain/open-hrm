import secrets
import string
from fastapi import (Request, APIRouter, Depends, HTTPException, Query, status,
                     BackgroundTasks)
from sqlalchemy.orm import Session
from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, EmailStr, Field, model_validator
from app.database import get_db
from app.schemas.user import (UserResponse, UserUpdate, UserCreate, UserDirectoryResponse,
                              EmploymentVerificationResponse)
from app.models.user import User, RoleEnum
from app.utils.email import send_welcome_email
from app.api.deps import (
    get_current_user, require_admin_or_hr, require_manager, is_privileged,
    assert_can_manage, assert_can_grant_role,
)
from app.core.security import get_password_hash, verify_password
from app.models.notification import NotificationTypeEnum
from app.utils.notifications import create_notification
from app.api.v1.auth import issue_refresh_token, revoke_all_sessions
from app.utils import audit

router = APIRouter()

# Fields a user must NOT be able to set on themselves — they drive pay
# (base_salary/allowance/hourly_rate/overtime/standard_hours), employment
# structure (department/designation/employee_id), or authority
# (role/manager_id/is_active). Only an admin may change these, via PUT /{id}.
# Personal-identity fields (CNIC, phone, address, etc.) are deliberately NOT
# here: those are the employee's own profile, editable via self-service.
ADMIN_ONLY_USER_FIELDS = {
    "role", "manager_id", "is_active",
    "department", "designation",
    "base_salary", "allowance", "hourly_rate", "overtime_eligible", "standard_hours",
    "employee_id", "date_of_joining",
    "works_remotely", "attendance_exempt",
    # Authority flags. Without these here, anyone could PUT {"is_hr": true} to
    # their own record and walk into payroll.
    "is_hr", "is_lab_custodian",
}

# HR managers may maintain operational employment data, but never role,
# activation status, salary, allowance, employee identity, or banking data.
HR_MANAGED_EMPLOYMENT_FIELDS = {
    "department",
    "designation",
    "manager_id",
    "overtime_eligible",
    "standard_hours",
}


def is_hr_manager(user: User) -> bool:
    """
    Anyone with HR reach: an explicitly flagged HR manager, or an admin.

    An admin outranks HR everywhere else in this codebase, so a check that
    admitted HR but not an admin was an inversion waiting to surprise someone.
    The flag itself still means what it says — see is_hr_only(), which is what
    the two admin-reserved actions guard against.
    """
    return bool(user.is_hr) or user.role == RoleEnum.ADMIN


class AssignManagerRequest(BaseModel):
    manager_id: Optional[int] = None


class ResendWelcomeEmailRequest(BaseModel):
    email: EmailStr


def _validate_password_strength(password: str) -> None:
    """Shared password rules, so an admin-set password is as strong as a self-set one."""
    if not any(character.isupper() for character in password):
        raise ValueError("New password must include an uppercase letter")
    if not any(character.isdigit() for character in password):
        raise ValueError("New password must include a number")
    if not any(not character.isalnum() for character in password):
        raise ValueError("New password must include a special character")


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(..., min_length=1)
    new_password: str = Field(..., min_length=8, max_length=128)
    confirm_password: str = Field(..., min_length=8, max_length=128)

    @model_validator(mode="after")
    def validate_passwords(self):
        if self.new_password != self.confirm_password:
            raise ValueError("New password and confirmation do not match")
        _validate_password_strength(self.new_password)
        return self


class AdminSetPasswordRequest(BaseModel):
    """
    An admin resetting someone else's password. Deliberately has no
    current_password field: the whole point is that the employee has forgotten
    it, so requiring it would make the reset impossible.
    """
    new_password: str = Field(..., min_length=8, max_length=128)
    confirm_password: str = Field(..., min_length=8, max_length=128)

    @model_validator(mode="after")
    def validate_passwords(self):
        if self.new_password != self.confirm_password:
            raise ValueError("New password and confirmation do not match")
        _validate_password_strength(self.new_password)
        return self


# Fields a manager must not see on a subordinate: pay, banking and personal
# identifiers. GET /{user_id} already blanked these, but the list endpoints
# returned the same people in full — same data, same caller, no redaction —
# so the single-record guard was trivially bypassed by listing instead.
MANAGER_HIDDEN_FIELDS = (
    "account_number", "bank_number", "branch_code", "account_title",
    "base_salary", "allowance",
    "hourly_rate", "overtime_eligible", "standard_hours",
    "date_of_birth", "address",
    "cnic_number", "cnic_front_url", "cnic_back_url",
    "personal_email", "father_name",
    "emergency_contact_name", "emergency_contact_phone",
)


def without_sensitive_fields(user) -> UserResponse:
    """One record with pay, banking and personal identifiers removed."""
    safe = UserResponse.model_validate(user)
    for field in MANAGER_HIDDEN_FIELDS:
        if hasattr(safe, field):
            setattr(safe, field, None)
    return safe


# The original name, kept because it reads well at the manager call site.
redact_for_manager = without_sensitive_fields


def ensure_employment_code(user: User) -> str:
    """Give a user a verification code the first time one is needed."""
    if not user.employment_verification_code:
        user.employment_verification_code = secrets.token_urlsafe(12)
    return user.employment_verification_code


@router.get("/verify-employment/{code}", response_model=EmploymentVerificationResponse)
def verify_employment(code: str, db: Session = Depends(get_db)):
    """Confirm an employment letter is genuine from the code printed on it.

    Unauthenticated by design, mirroring GET /payslips/verify/{code}: a bank or
    landlord has to be able to check a letter without an HRM account.

    Returns only what confirms the letter — name, employee ID, designation,
    department, joining date and whether the person is still employed. Never
    salary, CNIC, address or contact details, because anyone holding a letter
    can call this, and a verification endpoint that profiles the employee would
    be a worse leak than the problem it solves.

    An unknown code answers `valid: false` rather than 404, so a caller cannot
    tell an expired code from one that never existed.
    """
    user = db.query(User).filter(
        User.employment_verification_code == code
    ).first() if code else None
    if not user:
        return EmploymentVerificationResponse(valid=False)
    return EmploymentVerificationResponse(
        valid=True,
        employee_name=user.full_name,
        employee_id=user.employee_id,
        designation=user.designation,
        department=user.department,
        date_of_joining=user.date_of_joining,
        # A former employee's letter was genuine when issued; the letter is
        # confirmed, and the reader is told the employment has ended rather
        # than being left to assume it continues.
        currently_employed=bool(user.is_active),
        verified_at=datetime.utcnow(),
    )


@router.get("/me", response_model=UserResponse)
def get_current_user_info(current_user: User = Depends(get_current_user)):
    """Get current user information"""
    return current_user


@router.post("/me/change-password")
def change_current_user_password(
    password_data: ChangePasswordRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Allow any signed-in user to securely change their own password.

    Changing a password ends every remembered session, on this device and any
    other. The reason is the case this feature is used for: somebody who
    thinks another person knows their password. If the sessions that password
    opened survive the change, the change has achieved nothing — whoever was
    already signed in stays signed in for another week.

    The caller is then given a fresh refresh token so that *this* device stays
    signed in, which is the behaviour people expect and the only reason it is
    safe to be this aggressive with the others. It is returned only if they
    had a remembered session to begin with; handing one to a caller who never
    asked to be remembered would create a persistent session out of nothing.
    """
    if not verify_password(password_data.current_password, current_user.hashed_password):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Current password is incorrect")
    if verify_password(password_data.new_password, current_user.hashed_password):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="New password must be different from the current password")

    current_user.hashed_password = get_password_hash(password_data.new_password)
    ended = revoke_all_sessions(db, current_user.id, "password_changed")
    replacement = (issue_refresh_token(db, current_user, request=request)
                   if ended else None)
    db.commit()

    audit.record(db, "user.password_changed", actor=current_user, request=request,
                 target_type="user", target_id=current_user.id,
                 detail={"sessions_ended": ended})

    return {
        "message": "Password changed successfully",
        # How many other remembered devices were signed out, so the portal can
        # say so rather than leaving it a silent side effect.
        "sessions_ended": max(ended - (1 if replacement else 0), 0),
        "refresh_token": replacement,
    }


@router.put("/me", response_model=UserResponse)
def update_current_user(
    user_data: UserUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Update current user's own profile (personal fields only)"""
    # Authority fields: reject explicitly so a caller learns they're not allowed.
    if user_data.role is not None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You cannot change your own role"
        )
    can_manage_employment = is_privileged(current_user) or is_hr_manager(current_user)

    if user_data.manager_id is not None and not can_manage_employment:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You cannot change your own manager assignment"
        )
    if user_data.is_active is not None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You cannot change your own active status"
        )

    # Salary/identity fields are silently dropped (not 403): the profile form
    # legitimately round-trips some of these values, so we ignore them here
    # rather than reject the whole save. Only an admin can change them.
    update_data = user_data.model_dump(exclude_unset=True)
    if update_data.get("manager_id") == 0:
        update_data["manager_id"] = None
    if not is_privileged(current_user):
        protected_fields = ADMIN_ONLY_USER_FIELDS
        if can_manage_employment:
            protected_fields = ADMIN_ONLY_USER_FIELDS - HR_MANAGED_EMPLOYMENT_FIELDS
        for field in protected_fields:
            update_data.pop(field, None)

    for field, value in update_data.items():
        setattr(current_user, field, value)

    db.commit()
    db.refresh(current_user)

    return current_user


@router.get("/", response_model=List[UserResponse])
def list_users(
    skip: int = 0,
    limit: int = 100,
    include: str = Query("summary", pattern="^(summary|sensitive)$"),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_manager)
):
    """
    List all users (Manager/Admin only). Excludes soft-deleted (inactive) users.

    Summary by default. This endpoint is read by half the portal — the shifts
    screen, the document screen, the leave and reimbursement queues — to put
    names against ids, and it was answering every one of them with the whole
    personnel file: CNIC, date of birth, home address, salary and bank
    account, for all twenty people. Being admin-only is not a reason to send
    it; none of those screens ever read a single one of those fields.

    Pass include=sensitive to get them, which is what the employee
    administration screen does through its own endpoint. A caller who may not
    see them does not get them either way.
    """
    if is_privileged(current_user):
        users = db.query(User).filter(User.is_active == True).offset(skip).limit(limit).all()
        if include != "sensitive":
            return [without_sensitive_fields(u) for u in users]
    elif current_user.role == RoleEnum.MANAGER:
        # Managers can see their subordinates, minus pay/banking/PII.
        users = db.query(User).filter(User.manager_id == current_user.id, User.is_active == True).offset(skip).limit(limit).all()
        return [redact_for_manager(u) for u in users]
    else:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not enough permissions"
        )
    
    return users


@router.get("/team", response_model=List[UserDirectoryResponse])
def get_team_members(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Get team members based on user role. Returns a LIMITED directory view
    (no banking, salary, or PII) — this is peer-visible data, so it must not
    leak teammates' financial/personal details.
    - Employee: Returns their team members from all teams they belong to
    - Manager: Returns all subordinates (users reporting to them)
    - Admin: Returns all users
    """
    from app.models.team import UserTeam
    
    if is_privileged(current_user):
        # Admin sees all active users
        users = db.query(User).filter(User.is_active == True).all()
        return users
    
    elif current_user.role == RoleEnum.MANAGER:
        # Manager sees all their active subordinates
        subordinates = db.query(User).filter(User.manager_id == current_user.id, User.is_active == True).all()
        return subordinates
    
    else:  # Employee
        # Employee sees team members from their teams
        # Get all teams the user belongs to
        user_teams = db.query(UserTeam).filter(UserTeam.user_id == current_user.id).all()
        team_ids = [ut.team_id for ut in user_teams]
        
        if not team_ids:
            return []  # User is not in any team
        
        # Get all users in those teams
        team_members_associations = db.query(UserTeam).filter(
            UserTeam.team_id.in_(team_ids)
        ).all()
        
        # Get unique user IDs
        user_ids = list(set([tm.user_id for tm in team_members_associations]))
        
        # Fetch the users (active only)
        team_members = db.query(User).filter(User.id.in_(user_ids), User.is_active == True).all()
        return team_members


# Pictures on the chart are loaded by <img>, which cannot send a bearer
# token — so they go out already signed. See app/utils/signed_urls.py.
from app.utils.signed_urls import sign_url  # noqa: E402


class OrgNode(BaseModel):
    """One person in the reporting tree, with whoever reports to them."""
    id: int
    full_name: str
    email: str
    designation: Optional[str] = None
    department: Optional[str] = None
    role: str
    is_hr: bool = False
    profile_picture_url: Optional[str] = None
    employee_id: Optional[str] = None
    reports: List["OrgNode"] = []


OrgNode.model_rebuild()


# Registered before /{user_id}: a single-segment path would otherwise be
# swallowed by that route and answered with a 422 about an invalid integer.
@router.get("/me/profile-completion")
def my_profile_completion(current_user: User = Depends(get_current_user)):
    """
    What is still missing from my own record.

    The same object that rides along on the login payload, on its own — so the
    completion screen can re-check after each save without refetching the whole
    user.
    """
    return current_user.profile_completion


@router.get("/incomplete-profiles")
def incomplete_profiles(db: Session = Depends(get_db),
                        current_user: User = Depends(require_admin_or_hr)):
    """
    Who still has gaps, and what they are. For HR to chase.

    Ordered by how much is missing, because the person with eight fields
    outstanding is a different conversation from the one missing a branch code.
    """
    rows = []
    for u in db.query(User).filter(User.is_active == True).all():  # noqa: E712
        state = u.profile_completion
        if state["is_complete"]:
            continue
        rows.append({
            "employee_id": u.id, "employee_name": u.full_name, "email": u.email,
            "designation": u.designation, "department": u.department,
            "percent": state["percent"],
            "required_missing": state["required_missing"],
            "missing": [m["label"] for m in state["missing_required"]],
            "last_reminded_at": u.profile_reminder_sent_at,
        })
    return sorted(rows, key=lambda r: (-r["required_missing"], r["employee_name"]))


@router.get("/org-chart", response_model=List[OrgNode])
def org_chart(
    include_inactive: bool = False,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    The reporting tree, built from manager_id.

    Returned as a list of roots rather than a single root: an org chart with
    nobody at the top, or with two founders at the top, is a real shape and
    should render rather than fail. Anyone reporting to somebody outside the
    returned set — a manager who has left, say — is treated as a root too, so
    no one silently disappears from the chart.
    """
    q = db.query(User)
    if not include_inactive:
        q = q.filter(User.is_active == True)  # noqa: E712
    people = q.order_by(User.full_name).all()

    nodes = {
        u.id: OrgNode(
            id=u.id, full_name=u.full_name, email=u.email,
            designation=u.designation, department=u.department,
            role=u.role.value if hasattr(u.role, "value") else str(u.role),
            is_hr=bool(getattr(u, "is_hr", False)),
            profile_picture_url=sign_url(u.profile_picture_url),
            employee_id=u.employee_id, reports=[])
        for u in people
    }

    roots = []
    for u in people:
        parent = nodes.get(u.manager_id) if u.manager_id else None
        # Guard against someone being their own manager, which would build a
        # node that contains itself and never serialise.
        if parent is not None and u.manager_id != u.id:
            parent.reports.append(nodes[u.id])
        else:
            roots.append(nodes[u.id])
    return roots


@router.get("/{user_id}", response_model=UserResponse)
def get_user(
    user_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Get user by ID. Returns 404 for soft-deleted (inactive) users."""
    user = db.query(User).filter(User.id == user_id).first()
    
    if not user or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )
    
    # Self and admin get the full record (incl. own/all banking & salary).
    if current_user.id == user_id or is_privileged(current_user):
        return user
    # A manager may view a subordinate, but NOT their banking/salary/PII —
    # return the record with those fields blanked out.
    elif current_user.role == RoleEnum.MANAGER and user.manager_id == current_user.id:
        return redact_for_manager(user)
    else:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not enough permissions"
        )


@router.put("/{user_id}", response_model=UserResponse)
def update_user(
    user_id: int,
    user_data: UserUpdate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Update user information. Returns 404 for soft-deleted users."""
    user = db.query(User).filter(User.id == user_id).first()
    # Fields whose change has a consequence somebody may later need explained.
    # Bank details are here for a specific reason: changing an account number
    # redirects a salary, which is the oldest payroll fraud there is, and until
    # now it left no trace at all. CNIC, joining date and designation are here
    # because they are printed on contracts and payslips, drive probation,
    # leave accrual and gratuity, and decide which review form somebody gets.
    # Phone numbers and addresses are deliberately not watched — one row per
    # profile tweak makes the trail unreadable, which is its own failure.
    _audit_watch = ("role", "is_hr", "base_salary", "allowance", "hourly_rate",
                    "manager_id", "is_active", "email",
                    "account_number", "account_title", "bank_number",
                    "cnic_number", "date_of_joining", "designation",
                    "department", "standard_hours")
    _before = {f: getattr(user, f, None) for f in _audit_watch} if user else {}
    
    if not user or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )
    
    can_manage_employment = is_hr_manager(current_user)

    # Users can update themselves. Admins can update anyone. HR managers may
    # update employment-only fields for other employees.
    if current_user.id != user_id and not is_privileged(current_user) and not can_manage_employment:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not enough permissions"
        )
    assert_can_manage(current_user, user)

    update_data = user_data.model_dump(exclude_unset=True)
    assert_can_grant_role(current_user, update_data.get("role"))
    if update_data.get("manager_id") == 0:
        update_data["manager_id"] = None

    # Non-admins (i.e. a user editing their own record here) cannot touch pay,
    # role, manager assignment, active status, or identity — strip those fields
    # so a crafted payload can't self-escalate salary or privileges.
    if not is_privileged(current_user):
        if can_manage_employment and current_user.id != user_id:
            update_data = {
                field: value
                for field, value in update_data.items()
                if field in HR_MANAGED_EMPLOYMENT_FIELDS
            }
        else:
            protected_fields = ADMIN_ONLY_USER_FIELDS
            if can_manage_employment:
                protected_fields = ADMIN_ONLY_USER_FIELDS - HR_MANAGED_EMPLOYMENT_FIELDS
            for field in protected_fields:
                update_data.pop(field, None)

    for field, value in update_data.items():
        setattr(user, field, value)

    db.commit()
    db.refresh(user)

    # Record only what actually changed, so the trail stays readable. See
    # _audit_watch above for which edits are worth an answer to "who did this?".
    _after = {f: getattr(user, f, None) for f in _audit_watch}
    _diff = audit.changed_fields(_before, _after, _audit_watch)
    if _diff:
        audit.record(db, "user.updated", actor=current_user, request=request,
                     target_type="user", target_id=user.id, detail=_diff)

    return user


@router.post("/{user_id}/reset-password")
def admin_reset_user_password(
    user_id: int,
    password_data: AdminSetPasswordRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin_or_hr),
):
    """
    Set a user's password directly (Admin only).

    No current password is required — this is the path for an employee who has
    forgotten theirs and asked via the login page. The employee is notified that
    their password was changed and by whom, so a reset can never happen silently.
    """
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    # An admin changing their own password must go through /me/change-password,
    # which verifies the current one.
    if user.id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Use the change-password screen to update your own password.",
        )
    assert_can_manage(current_user, user)

    user.hashed_password = get_password_hash(password_data.new_password)
    # A password reset exists to lock somebody out — either the employee who
    # forgot theirs, or whoever else had it. Leaving their refresh tokens
    # alive would mean the new password changes nothing for a session already
    # open somewhere.
    revoke_all_sessions(db, user.id, "password_reset")
    db.commit()

    create_notification(
        db=db,
        user_id=user.id,
        title="Your password was reset",
        message=(
            f"{current_user.full_name} set a new password for your account. "
            f"If you did not request this, contact HR immediately."
        ),
        notification_type=NotificationTypeEnum.WARNING,
    )

    audit.record(db, "user.password_reset", actor=current_user, request=request,
                 target_type="user", target_id=user.id,
                 detail={"target_email": user.email})

    return {
        "success": True,
        "message": f"Password updated for {user.full_name}. Share the new password with them securely.",
    }


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user(
    user_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin_or_hr)
):
    """Soft-delete user (Admin only). Sets is_active=False; user is excluded from lists and cannot log in."""
    user = db.query(User).filter(User.id == user_id).first()
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )
    
    assert_can_manage(current_user, user)

    # Don't allow deleting yourself
    if user.id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete your own account"
        )
    
    # Soft delete: deactivate instead of removing the row
    user.is_active = False
    # And take away the sessions they already hold. Without this, a
    # deactivated account's phone goes on minting fresh access tokens from
    # its refresh token for the rest of the week — so "we removed their
    # access" and "they can no longer read anything" are days apart.
    ended = revoke_all_sessions(db, user.id, "user_deactivated")
    db.commit()

    audit.record(db, "user.deactivated", actor=current_user, request=request,
                 target_type="user", target_id=user.id,
                 detail={"target_email": user.email,
                         "target_name": user.full_name,
                         "sessions_ended": ended})

    return None


@router.get("/subordinates/list", response_model=List[UserResponse])
def get_my_subordinates(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_manager)
):
    """Get list of subordinates for the current manager (active only)"""
    subordinates = db.query(User).filter(User.manager_id == current_user.id, User.is_active == True).all()
    if is_privileged(current_user, db):
        return subordinates
    return [redact_for_manager(u) for u in subordinates]


# ==================== ADMIN ENDPOINTS ====================

@router.post("/", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
def create_user(
    user_data: UserCreate,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin_or_hr),
):
    """Create a new user (Admin or HR)"""
    assert_can_grant_role(current_user, user_data.role)

    # Check if user already exists
    existing_user = db.query(User).filter(User.email == user_data.email).first()
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already registered"
        )
    
    # Check if employee_id already exists
    if user_data.employee_id:
        existing_emp = db.query(User).filter(User.employee_id == user_data.employee_id).first()
        if existing_emp:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Employee ID already exists"
            )
    
    # Resolve manager_id: 0 or None means no manager (NULL in DB)
    manager_id = user_data.manager_id if user_data.manager_id else None
    
    # Validate manager_id if provided
    if manager_id is not None:
        manager = db.query(User).filter(User.id == manager_id).first()
        if not manager:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Manager not found"
            )
        if manager.role not in [RoleEnum.MANAGER, RoleEnum.ADMIN]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Assigned manager must have manager or admin role"
            )
    
    # Create new user
    hashed_password = get_password_hash(user_data.password)
    db_user = User(
        email=user_data.email,
        hashed_password=hashed_password,
        full_name=user_data.full_name,
        role=user_data.role,
        is_hr=bool(user_data.is_hr),
        gender=user_data.gender,
        phone=user_data.phone,
        address=user_data.address,
        date_of_birth=user_data.date_of_birth,
        marital_status=user_data.marital_status,
        anniversary_date=user_data.anniversary_date,
        cnic_number=user_data.cnic_number,
        employee_id=user_data.employee_id,
        department=user_data.department,
        designation=user_data.designation,
        emergency_contact_name=user_data.emergency_contact_name,
        emergency_contact_phone=user_data.emergency_contact_phone,
        manager_id=manager_id,
        # Pay defaults set at creation so payslip generation can prefill them.
        base_salary=user_data.base_salary,
        allowance=user_data.allowance,
        overtime_eligible=user_data.overtime_eligible,
        standard_hours=user_data.standard_hours,
        profile_picture_url=user_data.profile_picture_url,
        linkedin_welcome_picture_url=user_data.linkedin_welcome_picture_url,
        cnic_front_url=user_data.cnic_front_url,
        cnic_back_url=user_data.cnic_back_url,
        # Eight fields the schema accepted and this constructor dropped on the
        # floor. A caller sending them got a 201 and a record without them,
        # which is the worst way to lose data — it looks like it worked.
        #
        # father_name is the one that bites: a contract prints "X d/o Y" from
        # it, so a joiner created through this endpoint got an agreement with
        # their parentage missing. date_of_joining has a comment on the schema
        # explaining it exists so an admin can backfill a real historical join
        # date, and then it was never read — everybody got the day the row was
        # written. The banking fields decide where somebody's salary goes.
        father_name=user_data.father_name,
        personal_email=user_data.personal_email,
        works_remotely=user_data.works_remotely,
        bank_number=user_data.bank_number,
        account_title=user_data.account_title,
        account_number=user_data.account_number,
        branch_code=user_data.branch_code,
        **({"date_of_joining": user_data.date_of_joining}
           if user_data.date_of_joining else {}),
    )
    
    db.add(db_user)
    db.commit()
    db.refresh(db_user)
    
    # Every employee gets a verification code up front, so an employment
    # letter can be issued without a separate provisioning step.
    ensure_employment_code(db_user)
    db.commit()
    db.refresh(db_user)

     # Send welcome email (dummy print)
    background_tasks.add_task(
        send_welcome_email,
        to_email=user_data.email,
        full_name=user_data.full_name,
        password=user_data.password
    )

    return db_user


def _generate_temp_password(length: int = 12) -> str:
    """Generate a secure random temporary password"""
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


@router.post("/resend-welcome-email")
def resend_welcome_email(
    request: ResendWelcomeEmailRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin_or_hr),
):
    """Resend welcome email with new temporary password (Admin only).
    Use when a user missed the initial welcome email. Generates a new temp password
    and emails it to the user."""
    user = db.query(User).filter(User.email == request.email).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User with this email not found",
        )

    temp_password = _generate_temp_password()
    user.hashed_password = get_password_hash(temp_password)
    db.commit()

    background_tasks.add_task(
        send_welcome_email,
        to_email=user.email,
        full_name=user.full_name,
        password=temp_password,
    )

    return {
        "message": "Welcome email has been resent successfully",
        "email": user.email,
    }


@router.put("/{user_id}/assign-manager", response_model=UserResponse)
def assign_manager(
    user_id: int,
    request: AssignManagerRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin_or_hr)
):
    """Assign or remove manager for a user (Admin/HR). Returns 404 for soft-deleted users."""
    user = db.query(User).filter(User.id == user_id).first()

    if not user or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )
    assert_can_manage(current_user, user)
    
    # If manager_id is None, remove the manager assignment
    if request.manager_id is None:
        user.manager_id = None
    else:
        # Validate manager exists, is active, and has appropriate role
        manager = db.query(User).filter(User.id == request.manager_id).first()
        if not manager or not manager.is_active:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Manager not found"
            )
        if manager.role not in [RoleEnum.MANAGER, RoleEnum.ADMIN]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Assigned manager must have manager or admin role"
            )
        if request.manager_id == user_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="User cannot be their own manager"
            )
        # Walk the proposed manager's reporting chain. If it reaches the user,
        # the assignment would create a cycle (for example A -> B -> A).
        ancestor = manager
        visited_ids = set()
        while ancestor and ancestor.id not in visited_ids:
            if ancestor.id == user_id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="This assignment would create a circular reporting relationship"
                )
            visited_ids.add(ancestor.id)
            ancestor = (
                db.query(User).filter(User.id == ancestor.manager_id).first()
                if ancestor.manager_id
                else None
            )
        user.manager_id = request.manager_id
    
    db.commit()
    db.refresh(user)
    
    return user


@router.put("/{user_id}/reactivate", response_model=UserResponse)
def reactivate_user(
    user_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin_or_hr),
):
    """Reactivate a soft-deleted user (Admin only). Sets is_active=True so the user can log in and appear in lists again."""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )
    if user.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User is already active",
        )
    assert_can_manage(current_user, user)
    user.is_active = True
    db.commit()
    db.refresh(user)
    return user


@router.get("/admin/all", response_model=List[UserResponse])
def get_all_users_admin(
    skip: int = 0,
    limit: int = 1000,  # Increased limit for dashboard
    # Every other caller of this endpoint (payroll's employee picker, tax
    # certificates, dashboards) wants active employees only, by default, so
    # that stays the default. The Manage Users screen is the one place an
    # admin needs to find a suspended account again to reactivate it — a
    # suspended user was previously excluded from this list entirely with no
    # way to opt back in, which made reactivating them impossible from the UI.
    include_inactive: bool = False,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin_or_hr)
):
    """Get all users for admin dashboard (Admin only). Excludes soft-deleted users unless include_inactive=true."""
    query = db.query(User)
    if not include_inactive:
        query = query.filter(User.is_active == True)
    users = query.order_by(User.created_at.desc()).offset(skip).limit(limit).all()
    return users


@router.get("/admin/dashboard", response_model=List[UserResponse])
def get_dashboard_users(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin_or_hr)
):
    """
    Get all users for admin dashboard (Admin only)
    Returns all active users without pagination for dashboard display
    """
    users = db.query(User).filter(User.is_active == True).order_by(User.created_at.desc()).all()
    return users


@router.get("/admin/managers", response_model=List[UserResponse])
def get_all_managers(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin_or_hr)
):
    """Get all active users with manager or admin role (Admin only)"""
    managers = db.query(User).filter(
        User.role.in_([RoleEnum.MANAGER, RoleEnum.ADMIN]),
        User.is_active == True
    ).all()
    return managers


@router.get("/admin/employees", response_model=List[UserResponse])
def get_all_employees(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin_or_hr)
):
    """Get all active employees (excluding admins) - for manager assignment dropdown (Admin only)"""
    employees = db.query(User).filter(
        User.role != RoleEnum.ADMIN,
        User.is_active == True
    ).all()
    return employees


@router.get("/admin/all-detailed", response_model=List[UserResponse])
def get_all_users_detailed(
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin_or_hr)
):
    """
    Get all active users with detailed information for Manage Users tab (Admin only)
    Returns: Employee Name, Designation/Role, Department, Status (active/inactive)
    """
    users = db.query(User).filter(User.is_active == True).offset(skip).limit(limit).all()
    return users


@router.put("/{user_id}/documents", response_model=UserResponse)
def update_user_documents(
    user_id: int,
    profile_picture_url: Optional[str] = None,
    linkedin_welcome_picture_url: Optional[str] = None,
    cnic_front_url: Optional[str] = None,
    cnic_back_url: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Update user documents and pictures
    - Users can update their own documents
    - Admins can update anyone's documents
    
    Documents:
    - profile_picture_url: Profile picture with blue background, front facing
    - linkedin_welcome_picture_url: Picture for LinkedIn welcome post
    - cnic_front_url: CNIC (National ID) front side
    - cnic_back_url: CNIC (National ID) back side
    """
    user = db.query(User).filter(User.id == user_id).first()
    
    if not user or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )
    
    # Users can update their own documents, admin can update anyone's
    assert_can_manage(current_user, user)
    if current_user.id != user_id and not is_privileged(current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only update your own documents"
        )
    
    # Update document fields if provided
    if profile_picture_url is not None:
        user.profile_picture_url = profile_picture_url
    if linkedin_welcome_picture_url is not None:
        user.linkedin_welcome_picture_url = linkedin_welcome_picture_url
    if cnic_front_url is not None:
        user.cnic_front_url = cnic_front_url
    if cnic_back_url is not None:
        user.cnic_back_url = cnic_back_url
    
    db.commit()
    db.refresh(user)
    
    return user

