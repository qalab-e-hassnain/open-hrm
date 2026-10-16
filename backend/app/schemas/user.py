from pydantic import BaseModel, EmailStr, Field, field_serializer, field_validator

from app.schemas.datetimes import NaiveUTC
from typing import List, Literal, Optional
from datetime import datetime
from app.models.user import RoleEnum, GenderEnum

# Marital status is a plain string rather than a DB enum so it can grow
# (divorced, widowed) without an irreversible Postgres enum change.
MaritalStatus = Literal["single", "married"]


# Base User Schema
class UserBase(BaseModel):
    email: EmailStr
    full_name: str
    # Optional at the base level so existing users (created before this field
    # existed) still serialize fine; UserCreate below makes it required.
    gender: Optional[GenderEnum] = None
    phone: Optional[str] = None
    address: Optional[str] = None
    date_of_birth: Optional[NaiveUTC] = None
    # "married" unlocks maternity/paternity leave and the anniversary greeting.
    marital_status: Optional[MaritalStatus] = None
    anniversary_date: Optional[NaiveUTC] = None
    cnic_number: Optional[str] = None  # CNIC (National ID) number
    father_name: Optional[str] = None
    # Distinct from `email` (the work/login address) — personal correspondence only.
    personal_email: Optional[str] = None
    employee_id: Optional[str] = None
    department: Optional[str] = None  # Department name
    designation: Optional[str] = None  # Job title/designation
    emergency_contact_name: Optional[str] = None
    emergency_contact_phone: Optional[str] = None
    # Monthly salary. Payroll runs snapshot this into each entry at creation.
    base_salary: Optional[float] = 0.0
    # Recurring monthly allowance; prefills payslip generation.
    allowance: Optional[float] = 0.0
    overtime_eligible: Optional[bool] = True
    works_remotely: Optional[bool] = False
    attendance_exempt: Optional[bool] = False
    standard_hours: Optional[float] = 8.0
    # Documents & Pictures
    profile_picture_url: Optional[str] = None  # Profile picture with blue background, front facing
    linkedin_welcome_picture_url: Optional[str] = None  # Picture for LinkedIn welcome post
    cnic_front_url: Optional[str] = None  # CNIC front side
    cnic_back_url: Optional[str] = None  # CNIC back side
    # Banking information
    account_number: Optional[str] = None
    bank_number: Optional[str] = None
    branch_code: Optional[str] = None
    account_title: Optional[str] = None


# User Create Schema

    # A browser loading an <img> sends no Authorization header, so an
    # unsigned link to a private file renders as a broken image. These two
    # fields carry a short-lived signature on the way out so they just work.
    #
    # Only these two. A profile picture is directory information — the whole
    # point of an org chart is that colleagues can see it — while CNIC scans
    # and receipts stay behind the ownership check, reachable through
    # GET /upload/{name}/signed-url and only by someone entitled to them.
    @field_serializer("profile_picture_url", "linkedin_welcome_picture_url")
    def _sign_picture(self, value: Optional[str], _info) -> Optional[str]:
        from app.utils.signed_urls import sign_url
        return sign_url(value)


class UserCreate(UserBase):
    # 8 is the floor the self-service change already enforced
    # (ChangePasswordRequest); admin-created accounts were allowed 6, so the
    # weakest passwords in the system were the ones handed to new staff.
    password: str = Field(..., min_length=8, max_length=128)
    role: RoleEnum = RoleEnum.EMPLOYEE
    manager_id: Optional[int] = None
    # Required on creation (overrides UserBase's optional gender) so leave
    # eligibility (maternity/paternity) is always known for new users.
    gender: GenderEnum
    # Required too: it drives the birthday greeting and is on every HR record.
    date_of_birth: NaiveUTC
    # Optional: falls back to the model's `default=datetime.utcnow` (row
    # creation time) when omitted, but must be settable so admins can
    # backfill a real historical join date for employees who predate this
    # HRM system instead of getting today's date.
    date_of_joining: Optional[NaiveUTC] = None
    # Flags this user as an HR manager (admin-level reach, minus admin accounts).
    is_hr: Optional[bool] = False

    @field_validator("anniversary_date")
    @classmethod
    def anniversary_needs_marriage(cls, value, info):
        if value is not None and info.data.get("marital_status") != "married":
            raise ValueError("An anniversary date only applies to a married employee")
        return value


# User Update Schema
class UserUpdate(BaseModel):
    full_name: Optional[str] = None
    gender: Optional[GenderEnum] = None
    phone: Optional[str] = None
    address: Optional[str] = None
    date_of_birth: Optional[NaiveUTC] = None
    marital_status: Optional[MaritalStatus] = None
    anniversary_date: Optional[NaiveUTC] = None
    cnic_number: Optional[str] = None
    father_name: Optional[str] = None
    personal_email: Optional[str] = None
    date_of_joining: Optional[NaiveUTC] = None
    department: Optional[str] = None
    designation: Optional[str] = None
    emergency_contact_name: Optional[str] = None
    emergency_contact_phone: Optional[str] = None
    manager_id: Optional[int] = None
    role: Optional[RoleEnum] = None
    is_active: Optional[bool] = None
    base_salary: Optional[float] = None
    allowance: Optional[float] = None
    overtime_eligible: Optional[bool] = None
    is_hr: Optional[bool] = None
    # Runs the lab store. Admin-only, like is_hr (see ADMIN_ONLY_USER_FIELDS).
    is_lab_custodian: Optional[bool] = None
    standard_hours: Optional[float] = None
    # Banking information
    account_number: Optional[str] = None
    bank_number: Optional[str] = None
    branch_code: Optional[str] = None
    account_title: Optional[str] = None
    # Documents & Pictures
    profile_picture_url: Optional[str] = None
    linkedin_welcome_picture_url: Optional[str] = None
    cnic_front_url: Optional[str] = None
    cnic_back_url: Optional[str] = None


# User Response Schema
class ProfileCompletionField(BaseModel):
    field: str
    label: str
    why: str


class ProfileCompletion(BaseModel):
    """
    How much of an employee's own record they have filled in.

    Rides along on every UserResponse — including the one inside the login
    payload — so the frontend can put the completion screen in front of
    somebody at sign-in without a second request.
    """
    is_complete: bool
    percent: int
    required_total: int
    required_missing: int
    missing_required: List[ProfileCompletionField] = []
    missing_recommended: List[ProfileCompletionField] = []
    prompt_on_login: bool = False


class UserResponse(UserBase):
    id: int
    # One field for the UI to gate HR screens on, instead of asking the client
    # to recombine role and is_hr and get it subtly wrong. True for admins and
    # for flagged HR managers alike.
    has_hr_access: bool = False
    # Printed on the employment letter; the public verify endpoint reads it.
    employment_verification_code: Optional[str] = None
    role: RoleEnum
    manager_id: Optional[int] = None
    is_active: bool
    is_hr: bool = False
    # The portal shows Receiving and the custodian screens on this.
    is_lab_custodian: bool = False
    # True while covering full admin authority for an admin on approved leave
    # today — see app.models.user.User.is_acting_admin.
    is_acting_admin: bool = False
    # Derived (basic / 160), read-only — see models.user._derive_hourly_rate.
    hourly_rate: Optional[float] = 0.0
    date_of_joining: NaiveUTC
    created_at: datetime
    
        # Computed on the User model, so it is present on every response
    # built from it — /users/me, the login payload, the admin list.
    profile_completion: Optional[ProfileCompletion] = None

    class Config:
        from_attributes = True


# Limited directory view — safe to expose to peers/teammates. Deliberately
# EXCLUDES banking (account_number/bank_number/branch_code/account_title),
# salary/overtime (base_salary/allowance/hourly_rate/overtime_eligible/standard_hours), and other
# PII (date_of_birth/address/emergency_contact*). Used by endpoints that
# return other people's records to non-admin callers.
class UserDirectoryResponse(BaseModel):
    id: int
    full_name: str
    email: EmailStr
    role: RoleEnum
    gender: Optional[GenderEnum] = None
    department: Optional[str] = None
    designation: Optional[str] = None
    phone: Optional[str] = None
    employee_id: Optional[str] = None
    manager_id: Optional[int] = None
    is_active: bool
    profile_picture_url: Optional[str] = None

    @field_serializer("profile_picture_url")
    def _sign_picture(self, value: Optional[str], _info) -> Optional[str]:
        from app.utils.signed_urls import sign_url
        return sign_url(value)

    class Config:
        from_attributes = True


# Login Schema
class UserLogin(BaseModel):
    email: EmailStr
    password: str
    # "Remember this device": only when true does the backend issue a
    # refresh token, so an unchecked login can't silently outlive its
    # short-lived access token.
    remember_device: bool = False


# Token Schema
class Token(BaseModel):
    access_token: str
    # Absent when the login didn't opt into "remember this device".
    refresh_token: Optional[str] = None
    token_type: str = "bearer"
    user: UserResponse


# Token Data
class TokenData(BaseModel):
    email: Optional[str] = None



class EmploymentVerificationResponse(BaseModel):
    """
    Public answer to "is this employment letter genuine?".

    Deliberately narrow: enough to confirm the letter, never enough to profile
    someone. No salary, no CNIC, no contact details, no address.
    """
    valid: bool
    employee_name: Optional[str] = None
    employee_id: Optional[str] = None
    designation: Optional[str] = None
    department: Optional[str] = None
    date_of_joining: Optional[NaiveUTC] = None
    currently_employed: Optional[bool] = None
    verified_at: Optional[datetime] = None
