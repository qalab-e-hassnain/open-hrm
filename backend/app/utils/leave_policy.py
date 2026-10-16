"""
Leave policy
Based on policy effective October 16, 2024
"""
import calendar
import math
from datetime import datetime, timedelta, date
from typing import Dict


# Leave Policy Constants
class LeavePolicyConfig:
    # Annual Leave — cut from 24 to 14 by the revised policy effective
    # 01 January 2026, adopted instead of adding a Saturday shift or
    # extending daily hours to close the 35h/40h weekly gap.
    ANNUAL_LEAVE_PER_YEAR = 14  # Working days
    ANNUAL_LEAVE_PER_MONTH = 14 / 12  # Accrual rate
    # Nothing carries over. A year's unused annual leave ends with the year.
    ANNUAL_LEAVE_MAX_CARRYOVER = 0
    
    # Sick Leave. Under the revised policy sick leave applies only where there
    # was a hospital admission or a doctor's prescription — documentation is
    # expected for every sick day, not only for a run of them, and undocumented
    # sick leave is adjusted against personal or annual instead.
    SICK_LEAVE_PER_YEAR = 15  # Working days
    SICK_LEAVE_MEDICAL_CERT_THRESHOLD = 0  # Documentation expected from day one
    
    # Personal Leave
    PERSONAL_LEAVE_PER_MONTH = 1  # 1 day per month
    PERSONAL_LEAVE_PER_YEAR = 12  # Total per year
    
    # Parental Leave
    MATERNITY_LEAVE_WEEKS = 12  # 60 working days
    MATERNITY_LEAVE_DAYS = 60
    PATERNITY_LEAVE_WEEKS = 2  # 10 working days
    PATERNITY_LEAVE_DAYS = 10
    
    # Bereavement Leave
    BEREAVEMENT_LEAVE_MAX = 5  # Up to 5 days
    
    # Probation period
    PROBATION_MONTHS = 3  # First 3 months
    
    # Penalties
    UNAPPROVED_LEAVE_DEDUCTION = 5000  # Rs per day
    
    # Advance notice requirements. Annual leave keeps the two-week rule;
    # personal leave is no longer automatic and needs 48 hours, with genuine
    # emergencies considered on the day.
    PLANNED_LEAVE_NOTICE_DAYS = 14  # 2 weeks, annual/maternity/paternity
    PERSONAL_LEAVE_NOTICE_HOURS = 48  # "casual" in the data model

    # Only one member of a team may be off on a given day. Advisory rather
    # than blocking: the policy allows an exception once the team has agreed
    # among themselves, so the decision belongs to the approver.
    ONE_PER_TEAM_PER_DAY = True


def calculate_working_days(start_date: datetime, end_date: datetime) -> int:
    """
    Calculate number of working days between two dates
    Excludes weekends (Saturday, Sunday)
    
    Args:
        start_date: Start date
        end_date: End date
    
    Returns:
        Number of working days
    """
    if start_date > end_date:
        return 0
    
    working_days = 0
    current = start_date.date() if isinstance(start_date, datetime) else start_date
    end = end_date.date() if isinstance(end_date, datetime) else end_date
    
    while current <= end:
        # Monday=0, Sunday=6; Saturday=5, Sunday=6 are weekends
        if current.weekday() < 5:  # Monday to Friday
            working_days += 1
        current += timedelta(days=1)
    
    return working_days


def is_in_probation(date_of_joining: datetime) -> bool:
    """
    Check if employee is still in probation period (first 3 months)
    
    Args:
        date_of_joining: Employee's joining date (can be None)
    
    Returns:
        True if in probation, False otherwise
        Returns False if date_of_joining is None (can't determine probation status)
    """
    if not date_of_joining:
        return False  # Can't determine probation without joining date, so assume not in probation
    
    probation_end = date_of_joining + timedelta(days=LeavePolicyConfig.PROBATION_MONTHS * 30)
    return datetime.utcnow() < probation_end


def is_in_probation_for(user) -> bool:
    """
    Probation status for an actual employee.

    Prefers what HR recorded over what the joining date implies: someone
    confirmed early is confirmed, and someone whose probation was extended is
    still on it past the default window. Falls back to the old inference when
    nothing has been recorded, which is every employee predating the
    confirmation flow.
    """
    status = getattr(user, "probation_status", None)
    if status in ("confirmed", "not_applicable"):
        return False
    end = getattr(user, "probation_end_date", None)
    if end is not None:
        return date.today() < end
    if status == "extended":
        # Extended but with no date recorded — treat as still on probation
        # rather than silently confirming somebody.
        return True
    return is_in_probation(getattr(user, "date_of_joining", None))


def months_served_in_year(date_of_joining, year: int, on=None) -> int:
    """
    Whole calendar months served inside one year.

    A month is credited when the month itself ends, and only if the employee
    was there for the whole of it. Counting elapsed months from a date instead
    left somebody employed all year on eleven months at 31 December, because
    the twelfth had not "elapsed" until 1 January — a full year of service that
    never earned a full year of leave.

    Joining part-way through a month means that month is not credited. Joining
    on the 1st means it is.
    """
    if not date_of_joining:
        return 0
    joined = date_of_joining.date() if isinstance(date_of_joining, datetime) else date_of_joining
    today = on or date.today()
    if joined.year > year:
        return 0

    served = 0
    for month in range(1, 13):
        first = date(year, month, 1)
        last = date(year, month, calendar.monthrange(year, month)[1])
        if joined <= first and last <= today:
            served += 1
    return served


def calculate_accrued_annual_leave(date_of_joining, year: int = None, on=None) -> float:
    """
    Annual leave earned so far in one year.

    Earned by the month rather than granted in full on 1 January: somebody who
    joins in August has served part of the year and has part of the
    entitlement. It resets every January, which is why the count starts there
    and not at the joining date — the previous version counted months since
    joining across all years, so a two-year employee accrued twenty-four
    months' worth and was silently capped.

    Nothing carries over. A year's unused leave ends with the year.
    """
    year = year or (on or date.today()).year
    months = months_served_in_year(date_of_joining, year, on)
    accrued = months * LeavePolicyConfig.ANNUAL_LEAVE_PER_MONTH
    # Rounded up, not to the nearest. Leave is taken in whole days, and a
    # balance of 4.67 means somebody can book four days and argue about the
    # fifth. Rounding up settles it in the employee's favour, which is the only
    # direction a rounding rule should go when the company wrote the rule.
    accrued = math.ceil(round(accrued, 6))
    return float(min(accrued, LeavePolicyConfig.ANNUAL_LEAVE_PER_YEAR))


def calculate_accrued_personal_leave(current_month: int, current_year: int) -> float:
    """
    Calculate accrued personal leave for current year
    Accrual: 1 day per month
    
    Args:
        current_month: Current month (1-12)
        current_year: Current year
    
    Returns:
        Accrued personal leave days
    """
    # 1 day per month worked in current year
    return float(current_month)


def requires_medical_certificate(leave_type: str, total_days: int) -> bool:
    """
    Check if medical certificate is required
    Required for sick leave > 2 consecutive days
    
    Args:
        leave_type: Type of leave
        total_days: Number of consecutive days
    
    Returns:
        True if medical certificate required
    """
    if leave_type == "sick" and total_days > LeavePolicyConfig.SICK_LEAVE_MEDICAL_CERT_THRESHOLD:
        return True
    return False


def is_personal_notice_sufficient(request_date: datetime, leave_start: datetime) -> bool:
    """
    48 hours' notice for personal ("casual") leave.

    Measured in hours rather than whole days: a request made on Monday evening
    for Wednesday morning satisfies the policy, but a day-granularity check
    would score it as 1 day and wrongly flag it.
    """
    hours_in_advance = (leave_start - request_date).total_seconds() / 3600
    return hours_in_advance >= LeavePolicyConfig.PERSONAL_LEAVE_NOTICE_HOURS


def is_advance_notice_sufficient(request_date: datetime, leave_start: datetime) -> bool:
    """
    Check if advance notice is sufficient (2 weeks for planned leaves)
    
    Args:
        request_date: Date when leave was requested
        leave_start: Leave start date
    
    Returns:
        True if sufficient notice given
    """
    days_in_advance = (leave_start - request_date).days
    return days_in_advance >= LeavePolicyConfig.PLANNED_LEAVE_NOTICE_DAYS


def validate_leave_balance(
    leave_type: str,
    requested_days: int,
    available_balance: float
) -> Dict:
    """
    Validate if employee has sufficient leave balance
    
    Args:
        leave_type: Type of leave
        requested_days: Days requested
        available_balance: Available balance for that leave type
    
    Returns:
        Dict with valid flag and message
    """
    if requested_days > available_balance:
        return {
            "valid": False,
            "message": f"Insufficient {leave_type} leave balance. Requested: {requested_days} days, Available: {available_balance} days"
        }
    
    return {
        "valid": True,
        "message": "Leave balance sufficient"
    }


def calculate_leave_deduction(unapproved_days: int) -> float:
    """
    Calculate salary deduction for unapproved leave
    Rs 5000 per day as per company policy
    
    Args:
        unapproved_days: Number of unapproved leave days
    
    Returns:
        Deduction amount in Rs
    """
    return unapproved_days * LeavePolicyConfig.UNAPPROVED_LEAVE_DEDUCTION


def get_leave_type_balance_field(leave_type: str) -> Dict[str, str]:
    """
    Map leave type to balance field names
    
    Returns:
        Dict with total_field, used_field, remaining_field
    """
    mapping = {
        "annual": {
            "total_field": "annual_total",
            "used_field": "annual_used",
            "remaining_property": "annual_remaining"
        },
        "sick": {
            "total_field": "sick_total",
            "used_field": "sick_used",
            "remaining_property": "sick_remaining"
        },
        "casual": {  # Map casual to personal
            "total_field": "personal_total",
            "used_field": "personal_used",
            "remaining_property": "personal_remaining"
        },
        "maternity": {
            "total_field": "maternity_total",
            "used_field": "maternity_used",
            "remaining_property": "maternity_remaining"
        },
        "paternity": {
            "total_field": "paternity_total",
            "used_field": "paternity_used",
            "remaining_property": "paternity_remaining"
        },
        "unpaid": {
            "total_field": None,  # Unlimited
            "used_field": None,
            "remaining_property": None
        },
        "bereavement": {
            "total_field": "bereavement_total",
            "used_field": "bereavement_used",
            "remaining_property": "bereavement_remaining"
        }
    }
    
    return mapping.get(leave_type, mapping["annual"])



# ---------------------------------------------------------------------------
# Eligibility
# ---------------------------------------------------------------------------

# Everyone gets these; the parental pair is earned by circumstance.
UNIVERSAL_LEAVE_TYPES = ("annual", "sick", "casual", "unpaid", "bereavement")


def parental_leave_for(user) -> str:
    """
    The parental leave this employee may take, or "" for none.

    Company rule: maternity/paternity apply to married employees only, and the
    gender then decides which of the two. An employee with no marital status on
    file is treated as not married — the leave is granted on a recorded fact,
    never on a guess.
    """
    if str(getattr(user, "marital_status", "") or "").strip().lower() != "married":
        return ""
    gender = getattr(user.gender, "value", user.gender)
    gender = str(gender or "").strip().lower()
    if gender == "female":
        return "maternity"
    if gender == "male":
        return "paternity"
    return ""


def eligible_leave_types(user) -> list:
    """Leave types this employee may actually request, for UI and validation."""
    parental = parental_leave_for(user)
    return list(UNIVERSAL_LEAVE_TYPES) + ([parental] if parental else [])
