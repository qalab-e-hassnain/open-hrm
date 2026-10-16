from pydantic import BaseModel, field_validator, model_validator

from app.schemas.datetimes import NaiveUTC
from typing import Optional, Union
from datetime import datetime
from app.models.leave import LeaveTypeEnum, LeaveStatusEnum


def parse_date_string(date_str: Union[str, datetime]) -> datetime:
    """Parse date string from various formats including DD/MM/YYYY"""
    if isinstance(date_str, datetime):
        return date_str
    
    if not isinstance(date_str, str):
        raise ValueError(f"Invalid date format: {date_str}")
    
    date_str = date_str.strip()
    
    # Try ISO format first (YYYY-MM-DD or YYYY-MM-DDTHH:MM:SS)
    try:
        # Handle ISO format with or without time
        if 'T' in date_str or len(date_str) == 10:
            return datetime.fromisoformat(date_str.replace('Z', '+00:00'))
    except (ValueError, AttributeError):
        pass
    
    # Try DD/MM/YYYY format (prioritize this as it's what the frontend uses)
    try:
        parts = date_str.split('/')
        if len(parts) == 3:
            day, month, year = map(int, parts)
            # Validate ranges
            if 1 <= day <= 31 and 1 <= month <= 12 and year >= 2000:
                return datetime(year, month, day)
    except (ValueError, IndexError):
        pass
    
    # Try MM/DD/YYYY format (US format) - only if first part <= 12
    try:
        parts = date_str.split('/')
        if len(parts) == 3:
            first, second, year = map(int, parts)
            # If first part is <= 12, try MM/DD/YYYY
            if first <= 12 and second <= 31:
                return datetime(year, first, second)
    except (ValueError, IndexError):
        pass
    
    # Try other common formats
    formats = [
        '%d-%m-%Y',
        '%Y-%m-%d',
        '%d.%m.%Y',
        '%Y.%m.%d',
        '%d %m %Y',
        '%Y %m %d',
    ]
    
    for fmt in formats:
        try:
            return datetime.strptime(date_str, fmt)
        except ValueError:
            continue
    
    raise ValueError(f"Unable to parse date: {date_str}. Expected formats: YYYY-MM-DD, DD/MM/YYYY, or ISO datetime")


# Base Leave Schema
class LeaveBase(BaseModel):
    leave_type: LeaveTypeEnum
    start_date: NaiveUTC
    end_date: NaiveUTC
    reason: Optional[str] = None
    comments: Optional[str] = None  # Additional comments/notes
    
    @field_validator('leave_type', mode='before')
    @classmethod
    def normalize_leave_type(cls, v: Union[str, LeaveTypeEnum]) -> LeaveTypeEnum:
        """Normalize leave type to handle variations"""
        if isinstance(v, LeaveTypeEnum):
            return v
        
        if not isinstance(v, str):
            raise ValueError(f"Invalid leave type: {v}")
        
        v_lower = v.lower().strip()
        
        # Map common variations
        type_mapping = {
            'personal': LeaveTypeEnum.CASUAL,
            'personal leave': LeaveTypeEnum.CASUAL,
            'casual': LeaveTypeEnum.CASUAL,
            'annual': LeaveTypeEnum.ANNUAL,
            'sick': LeaveTypeEnum.SICK,
            'sick leave': LeaveTypeEnum.SICK,
            'maternity': LeaveTypeEnum.MATERNITY,
            'paternity': LeaveTypeEnum.PATERNITY,
            'unpaid': LeaveTypeEnum.UNPAID,
        }
        
        if v_lower in type_mapping:
            return type_mapping[v_lower]
        
        # Try direct enum value
        try:
            return LeaveTypeEnum(v_lower)
        except ValueError:
            valid_types = ', '.join([e.value for e in LeaveTypeEnum])
            raise ValueError(
                f"Invalid leave type: '{v}'. "
                f"Valid types are: {valid_types}. "
                f"(Note: 'personal' maps to 'casual')"
            )
    
    @field_validator('start_date', 'end_date', mode='before')
    @classmethod
    def parse_date(cls, v: Union[str, datetime]) -> datetime:
        """Parse date from string in various formats"""
        return parse_date_string(v)
    
    @model_validator(mode='after')
    def validate_date_range(self):
        """Ensure start_date is before or equal to end_date (allows single-day leaves)"""
        if self.start_date > self.end_date:
            raise ValueError("Start date must be before or equal to end date")
        return self


# Leave Create Schema
class LeaveCreate(LeaveBase):
    medical_certificate_url: Optional[str] = None  # Upload medical certificate if required
    # Admin-only: who should cover full admin authority while this leave is
    # approved and active. Null keeps the default (HR covers). Rejected by the
    # endpoint unless the requester is a true admin — see
    # api.v1.leaves.create_leave_request.
    acting_admin_id: Optional[int] = None
    
    @field_validator('start_date', 'end_date', mode='after')
    @classmethod
    def validate_dates_not_past(cls, v: datetime) -> datetime:
        """Ensure dates are not in the past (today or future) - only for new leave creation"""
        today = datetime.utcnow().date()
        if v.date() < today:
            raise ValueError(f"Date {v.date()} is in the past. Please select today or a future date.")
        return v


# Leave Update Schema
class LeaveUpdate(BaseModel):
    leave_type: Optional[LeaveTypeEnum] = None
    start_date: Optional[NaiveUTC] = None
    end_date: Optional[NaiveUTC] = None
    reason: Optional[str] = None
    comments: Optional[str] = None
    
    @field_validator('start_date', 'end_date', mode='before')
    @classmethod
    def parse_date(cls, v: Union[str, datetime, None]) -> Optional[datetime]:
        """Parse date from string in various formats"""
        if v is None:
            return None
        return parse_date_string(v)


# Leave Response Schema
class LeaveResponse(LeaveBase):
    id: int
    employee_id: int
    employee_name: Optional[str] = None
    employee_email: Optional[str] = None
    status: LeaveStatusEnum
    total_days: Optional[int] = None
    medical_certificate_url: Optional[str] = None
    medical_certificate_required: bool = False
    # Manager approval
    manager_approved_by: Optional[int] = None
    manager_approver_name: Optional[str] = None  # Name of the manager who approved
    manager_approved_at: Optional[datetime] = None
    
    # HR/Admin final approval
    approved_by: Optional[int] = None
    approver_name: Optional[str] = None  # Name of the HR/Admin who approved
    approved_at: Optional[datetime] = None
    rejection_reason: Optional[str] = None
    comments: Optional[str] = None  # Additional comments/notes
    created_at: datetime
    updated_at: datetime
    warnings: Optional[list[str]] = None  # List of warnings for frontend display
    # Who covers full admin authority while this leave is approved and active
    # (admin leaves only). Null means HR covers by default.
    acting_admin_id: Optional[int] = None
    acting_admin_name: Optional[str] = None
    
    class Config:
        from_attributes = True


# Leave Approval Schema
class LeaveApproval(BaseModel):
    status: LeaveStatusEnum  # approved or rejected
    rejection_reason: Optional[str] = None
    comments: Optional[str] = None  # Additional comments when approving/rejecting

