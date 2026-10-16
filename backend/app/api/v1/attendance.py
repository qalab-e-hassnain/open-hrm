from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session
from typing import List
from datetime import datetime, timedelta
from app.database import get_db
from app.schemas.attendance import AttendanceCheckIn, AttendanceCheckOut, AttendanceResponse
from app.models.attendance import Attendance
from app.models.user import User, RoleEnum
from app.api.deps import get_current_user, require_manager, is_privileged
from app.core.geofencing import (is_within_office_boundary, get_distance_from_office,
                                 is_office_network, office_networks)
from app.utils import audit
from app.config import settings
from app.utils.attendance_session import (
    close_expired_sessions,
    day_sessions,
    is_auto_checkout,
    standard_hours_for,
)
from app.utils.notifications import notify_admins
from app.models.notification import NotificationTypeEnum

router = APIRouter()


def _day_start(moment: datetime = None) -> datetime:
    return (moment or datetime.utcnow()).replace(hour=0, minute=0, second=0, microsecond=0)


def _notify_admins_attendance(db: Session, user: User, event: str, detail: str) -> None:
    """
    Tell admins about an employee's attendance activity. Admins do not get a
    notification about their own check-in, and a failure here never blocks the
    check-in itself (notify_admins swallows its own errors).
    """
    if not settings.NOTIFY_ADMINS_ON_ATTENDANCE:
        return
    notify_admins(
        db=db,
        title=f"{user.full_name} {event}",
        message=detail,
        notification_type=NotificationTypeEnum.INFO,
        exclude_user_id=user.id,
    )


def day_type_for(check_date):
    """Weekday / weekend / holiday, without pricing anything."""
    from app.utils.overtime import is_holiday, is_weekend
    return "holiday" if is_holiday(check_date) else ("weekend" if is_weekend(check_date) else "weekday")


def _row_status(attendance, day) -> str:
    """
    Whether this row represents a completed day.

    A day is capped and closed automatically, so one day is often several
    rows. The question the badge answers is "did this person put their hours
    in", which belongs to the day — so an evening session on a day that
    already reached eight hours reads as completed, not as a 51-minute
    failure.

    "incomplete" still means what it always did: a session nobody has checked
    out of. "short" is new — checked out, but the day never reached its hours.
    """
    if attendance.check_out_time is None:
        return "incomplete"
    if day is None:
        return "completed"
    return "completed" if day.get("day_completed") else "short"


@router.get("/debug/geofencing")
def geofencing_debug(request: Request,
                     current_user: User = Depends(get_current_user)):
    """
    What this connection looks like to the server, for configuring
    OFFICE_IP_RANGES.

    Open it from a machine on the office wifi: `source_ip` is the address to
    put in the setting, and `matches_office_network` says whether the current
    value already covers it. The comment above ENFORCE_OFFICE_IP has pointed
    here since the flag was added, but the route did not exist and returned
    404 — so the one instruction for configuring this safely could not be
    followed.

    Read-only, and it reports the caller's own address only.
    """
    source_ip = audit.client_ip(request)
    configured = [str(net) for net in office_networks()]
    return {
        "source_ip": source_ip,
        "matches_office_network": is_office_network(source_ip),
        "configured_ranges": configured,
        "enforce_office_ip": settings.ENFORCE_OFFICE_IP,
        "office_geofence": {
            "latitude": settings.OFFICE_LATITUDE,
            "longitude": settings.OFFICE_LONGITUDE,
            "radius_metres": settings.OFFICE_RADIUS_METERS,
        },
        "note": (
            "Put source_ip in OFFICE_IP_RANGES as a CIDR — /32 for IPv4, /64 "
            "for IPv6, since the IPv6 host part rotates. The office is "
            "dual-stacked, so list both families: a request arrives on "
            "whichever the client picks."
        ),
    }


@router.post("/checkin", response_model=AttendanceResponse, status_code=status.HTTP_201_CREATED)
def check_in(
    checkin_data: AttendanceCheckIn,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Check-in with geofencing validation"""
    # Network check first. The GPS pair below is supplied by the client and can
    # be set to any value, so it is a usability aid, not proof of presence; the
    # source address is assigned by the network and is what actually ties a
    # check-in to the office. Enabled only once OFFICE_IP_RANGES is confirmed.
    source_ip = audit.client_ip(request)
    if settings.ENFORCE_OFFICE_IP and not is_office_network(source_ip):
        audit.record(db, "attendance.checkin_blocked", actor=current_user,
                     request=request, detail={"reason": "off_network", "ip": source_ip})
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Check-in is only allowed from the office network.",
        )
    # A remote employee is exempt from the geofence entirely. Without this the
    # check below refuses them outright — they are not near the office and
    # never will be, so there is no location they could send that would pass.
    if current_user.attendance_exempt:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Attendance is not recorded for this account.",
        )

    # Two independent signals can place somebody at the office, and either
    # will do. A desk PC has no GPS at all — navigator.geolocation answers
    # POSITION_UNAVAILABLE — so demanding a fix told people sitting at their
    # own desks that they were not in the office. The source address covers
    # exactly that case, and it is the better evidence of the two: the client
    # types the coordinates into the request and can send any pair it likes,
    # while the network assigns the address.
    on_office_network = is_office_network(source_ip)
    has_fix = checkin_data.latitude is not None and checkin_data.longitude is not None
    in_fence = has_fix and is_within_office_boundary(
        checkin_data.latitude, checkin_data.longitude)

    if not current_user.works_remotely and not in_fence and not on_office_network:
        if has_fix:
            distance = get_distance_from_office(
                checkin_data.latitude, checkin_data.longitude)
            detail = (f"You are {distance:.0f} m from the office, and this "
                      f"connection is not the office network.")
        else:
            # Say why, not just no. "You are not in the office" with no reason
            # is what sent people to HR in the first place.
            detail = ("This device could not provide a location, and this "
                      "connection is not the office network. Check in from "
                      "the office wifi, or from a device with location on.")
        audit.record(db, "attendance.checkin_blocked", actor=current_user,
                     request=request,
                     detail={"reason": "off_site", "has_fix": has_fix,
                             "ip": source_ip})
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail)

    verified_by = ("remote" if current_user.works_remotely
                   else "gps" if in_fence else "network")
    
    # Close any session that has already reached the working-day cap, so an
    # employee who worked a full day and never checked out can check in again
    # for overtime instead of being blocked.
    close_expired_sessions(db, employee_id=current_user.id)

    # Any session still open is under the cap and must be checked out manually
    existing_checkin = db.query(Attendance).filter(
        Attendance.employee_id == current_user.id,
        Attendance.check_out_time == None
    ).first()

    if existing_checkin:
        print(f"❌ User has an active check-in from {existing_checkin.check_in_time}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"You have an active check-in from {existing_checkin.check_in_time.strftime('%Y-%m-%d %H:%M')}. Please check out first before checking in again."
        )

    # A day's standard hours are consumed by the earliest sessions, so anything
    # checked in after the cap is already used up is an overtime session.
    cap = standard_hours_for(db, current_user.id)
    earlier_today = day_sessions(db, current_user.id, _day_start())
    hours_so_far = sum(session.duration_hours or 0.0 for session in earlier_today)
    is_overtime_session = hours_so_far >= cap

    # Create attendance record
    attendance = Attendance(
        employee_id=current_user.id,
        check_in_time=datetime.utcnow(),
        check_in_latitude=checkin_data.latitude,
        check_in_longitude=checkin_data.longitude,
        check_in_address=checkin_data.address,
        verified_by=verified_by,
    )

    db.add(attendance)
    db.commit()
    db.refresh(attendance)

    _notify_admins_attendance(
        db,
        current_user,
        "started an overtime session" if is_overtime_session else "checked in",
        f"Checked in at {attendance.check_in_time.strftime('%I:%M %p')} UTC"
        + (f" after {hours_so_far:.2f}h already worked today." if is_overtime_session else "."),
    )

    return attendance


@router.post("/checkout", response_model=AttendanceResponse)
def check_out(
    checkout_data: AttendanceCheckOut,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Check-out (can be done from anywhere)"""
    # Enforce the working-day cap first: if the session already ran past it, it
    # is closed at the cap and this request reports that rather than crediting
    # the extra time.
    auto_closed = close_expired_sessions(db, employee_id=current_user.id)

    # Find today's check-in record
    today_start = _day_start()

    print(f"\n=== CHECKOUT DEBUG ===")
    print(f"User: {current_user.email} (ID: {current_user.id})")
    print(f"Looking for check-in >= {today_start}")

    attendance = db.query(Attendance).filter(
        Attendance.employee_id == current_user.id,
        Attendance.check_in_time >= today_start,
        Attendance.check_out_time == None
    ).first()

    if not attendance:
        if auto_closed:
            capped = auto_closed[-1]
            print(f"ℹ️ Session was auto-closed at the {capped.duration_hours}h cap")
            print(f"=====================\n")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Your session was closed automatically at "
                    f"{capped.check_out_time.strftime('%I:%M %p')} after "
                    f"{capped.duration_hours:g} hours. Check in again to record overtime."
                )
            )

        # Check if user has any attendance today (even checked out)
        any_today = db.query(Attendance).filter(
            Attendance.employee_id == current_user.id,
            Attendance.check_in_time >= today_start
        ).first()

        if any_today:
            print(f"❌ REJECTED - Already checked out")
            print(f"Check-in: {any_today.check_in_time}, Check-out: {any_today.check_out_time}")
            print(f"=====================\n")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="You have already checked out for today"
            )
        else:
            print(f"❌ REJECTED - No check-in found")
            print(f"=====================\n")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No active check-in found for today. Please check-in first."
            )
    
    print(f"✅ Found check-in at {attendance.check_in_time}")
    print(f"=====================\n")
    
    # Update with check-out details
    checkout_time = datetime.utcnow()
    attendance.check_out_time = checkout_time
    attendance.check_out_latitude = checkout_data.latitude
    attendance.check_out_longitude = checkout_data.longitude
    attendance.check_out_address = checkout_data.address
    
    # Calculate duration in hours
    duration = checkout_time - attendance.check_in_time
    attendance.duration_hours = duration.total_seconds() / 3600

    db.commit()
    db.refresh(attendance)

    _notify_admins_attendance(
        db,
        current_user,
        "checked out",
        f"Checked out at {checkout_time.strftime('%I:%M %p')} UTC after "
        f"{attendance.duration_hours:.2f} hours.",
    )

    return attendance


@router.get("/", response_model=List[AttendanceResponse])
def list_attendance(
    skip: int = 0,
    limit: int = 100,
    start_date: datetime = None,
    end_date: datetime = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """List attendance records"""
    query = db.query(Attendance)
    
    # Filter by role
    if current_user.role == RoleEnum.EMPLOYEE:
        query = query.filter(Attendance.employee_id == current_user.id)
    elif current_user.role == RoleEnum.MANAGER:
        subordinate_ids = [s.id for s in current_user.subordinates] + [current_user.id]
        query = query.filter(Attendance.employee_id.in_(subordinate_ids))
    # Admin can see all
    
    # Filter by date range
    if start_date:
        query = query.filter(Attendance.check_in_time >= start_date)
    if end_date:
        query = query.filter(Attendance.check_in_time <= end_date)
    
    attendances = query.offset(skip).limit(limit).all()
    return attendances


@router.get("/filter")
def filter_attendance(
    date: str = None,  # Specific date (YYYY-MM-DD)
    start_date: str = None,  # Date range start
    end_date: str = None,  # Date range end
    month: int = None,  # Month (1-12)
    year: int = None,  # Year
    record_status: str = None,  # completed / incomplete
    employee_id: int = None,  # Specific employee (Manager/Admin only)
    exclude_self: bool = False,  # Drop the caller's own records
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Flexible attendance filtering endpoint

    Filter Options:
    - date: Specific date (YYYY-MM-DD)
    - start_date & end_date: Date range
    - month & year: Specific month
    - record_status: completed (checked out) or incomplete (not checked out)
    - employee_id: Specific employee (Manager/Admin only)
    - exclude_self: omit the caller's own attendance. Used by the "Employees"
      view of Attendance history, which reviews other people's hours — the
      reviewer's own records belong in their "My attendance" view instead.
      Applied server-side so the row limit and any totals derived from the
      response are not skewed by records that get dropped afterwards.

    Examples:
    - /attendance/filter?date=2024-11-12
    - /attendance/filter?start_date=2024-11-01&end_date=2024-11-30
    - /attendance/filter?month=11&year=2024
    - /attendance/filter?record_status=completed
    - /attendance/filter?month=7&year=2026&exclude_self=true
    """
    from app.utils.overtime import (
        approved_overtime_by_day, day_context, standard_hours_resolver,
    )
    from datetime import date as date_type
    import calendar

    # Enforce the working-day cap before reporting, so a stale open session is
    # never presented as still running. An employee's own read only touches
    # their own rows; a manager or admin listing the team sweeps all of them,
    # which is what makes the team view accurate.
    sweep_employee_id = employee_id
    if sweep_employee_id is None and current_user.role == RoleEnum.EMPLOYEE:
        sweep_employee_id = current_user.id
    close_expired_sessions(db, employee_id=sweep_employee_id)

    query = db.query(Attendance)

    # Role-based filtering
    if current_user.role == RoleEnum.EMPLOYEE:
        # Employees can only view their own attendance
        if employee_id and employee_id != current_user.id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You can only view your own attendance"
            )
        query = query.filter(Attendance.employee_id == current_user.id)
    elif current_user.role == RoleEnum.MANAGER:
        if employee_id:
            # Manager viewing specific employee
            # Allow managers to view their own attendance
            if employee_id == current_user.id:
                query = query.filter(Attendance.employee_id == employee_id)
            else:
                # Check if employee is a subordinate
                employee = db.query(User).filter(User.id == employee_id).first()
                if not employee:
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail="Employee not found"
                    )
                # Check if employee is a subordinate (manager_id matches current user)
                if employee.manager_id != current_user.id:
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail="You can only view attendance of your subordinates"
                    )
                query = query.filter(Attendance.employee_id == employee_id)
        else:
            # Manager viewing all subordinates (including themselves)
            subordinate_ids = [s.id for s in current_user.subordinates] + [current_user.id]
            query = query.filter(Attendance.employee_id.in_(subordinate_ids))
    else:  # Admin
        if employee_id:
            query = query.filter(Attendance.employee_id == employee_id)
        # Admin can see all, no additional filter needed

    # Applied after the role scoping above so it can only ever narrow what the
    # caller was already allowed to see, never widen it.
    if exclude_self:
        query = query.filter(Attendance.employee_id != current_user.id)

    # Date filters
    if date:
        # Specific date
        target_date = datetime.strptime(date, "%Y-%m-%d").date()
        date_start = datetime.combine(target_date, datetime.min.time())
        date_end = datetime.combine(target_date, datetime.max.time())
        query = query.filter(
            Attendance.check_in_time >= date_start,
            Attendance.check_in_time <= date_end
        )
    elif month and year:
        # Specific month
        month_start = datetime(year, month, 1)
        last_day = calendar.monthrange(year, month)[1]
        month_end = datetime(year, month, last_day, 23, 59, 59)
        query = query.filter(
            Attendance.check_in_time >= month_start,
            Attendance.check_in_time <= month_end
        )
    elif start_date or end_date:
        # Date range
        if start_date:
            start_dt = datetime.strptime(start_date, "%Y-%m-%d")
            query = query.filter(Attendance.check_in_time >= start_dt)
        if end_date:
            end_dt = datetime.strptime(end_date, "%Y-%m-%d")
            end_dt = end_dt.replace(hour=23, minute=59, second=59)
            query = query.filter(Attendance.check_in_time <= end_dt)
    
    # Status filter
    if record_status == "completed":
        query = query.filter(Attendance.check_out_time != None)
    elif record_status == "incomplete":
        query = query.filter(Attendance.check_out_time == None)
    
    # Order by most recent first
    query = query.order_by(Attendance.check_in_time.desc())
    
    # Get results
    attendances = query.offset(skip).limit(limit).all()
    
    # Hours are read against the whole employee-day, not the single row: a
    # capped day is stored as several sessions, and judging each one on its own
    # calls an evening session "not completed" for somebody who worked eleven
    # hours. Overtime is read from the signed claims, never from the clock.
    # Every employee on the page in one query, and FIRST: looked up row by row
    # this was forty-one queries for twenty-eight rows — the same handful of
    # people fetched over and over — and eight of the endpoint's nine seconds.
    # Loading them up front also fills the session's identity map, so the
    # per-employee standard-hours lookups below cost nothing.
    names = {}
    employee_ids = {a.employee_id for a in attendances}
    if employee_ids:
        names = {u.id: u for u in
                 db.query(User).filter(User.id.in_(employee_ids)).all()}

    day_hours = day_context(db, attendances, standard_hours_resolver(db))
    claimed_overtime = approved_overtime_by_day(db, attendances)

    # Enhanced response with overtime calculations
    results = []
    for attendance in attendances:
        employee = names.get(attendance.employee_id)

        # Calculate overtime if checked out
        overtime_data = {}
        if attendance.check_out_time:
            hours_calc = day_hours.get(attendance.id, {
                "total_hours": attendance.duration_hours or 0.0,
                "regular_hours": attendance.duration_hours or 0.0,
                "extra_hours": 0.0,
                "day_total_hours": attendance.duration_hours or 0.0,
                "day_required_hours": 0.0,
                "day_completed": False,
            })
            # Overtime is what has been claimed and signed off, not what the
            # clock ran past. People forget to check out, and a forgotten
            # checkout reported as three hours of overtime is a pay
            # expectation nobody agreed to. The unclaimed time is still
            # visible as `extra_hours` — a fact about the day, carrying no
            # money and no promise of any.
            claimed = claimed_overtime.get(
                (attendance.employee_id, attendance.check_in_time.date()), 0.0)
            overtime_data = {
                "working_hours": hours_calc["total_hours"],
                "regular_hours": hours_calc["regular_hours"],
                "overtime_hours": round(claimed, 2),
                "overtime_claimed": claimed > 0,
                "extra_hours": hours_calc["extra_hours"],
                "day_total_hours": hours_calc["day_total_hours"],
                "day_required_hours": hours_calc["day_required_hours"],
                "day_completed": hours_calc["day_completed"],
                "overtime_bonus": 0.0,
                "overtime_rate": 0.0,
                "day_type": day_type_for(attendance.check_in_time.date()),
                "auto_checked_out": is_auto_checkout(attendance),
            }

        result = {
            "id": attendance.id,
            "employee_id": attendance.employee_id,
            "employee_name": employee.full_name if employee else "Unknown",
            "date": attendance.check_in_time.date().isoformat(),
            "check_in_time": attendance.check_in_time.isoformat(),
            "check_out_time": attendance.check_out_time.isoformat() if attendance.check_out_time else None,
            "check_in_address": attendance.check_in_address,
            "check_out_address": attendance.check_out_address,
            # "completed" is a statement about the day, not the row. A second
            # session on a day that already met its hours is part of a
            # completed day; "short" is a day that was checked out of without
            # reaching them, and "incomplete" a session still running.
            "status": _row_status(attendance, day_hours.get(attendance.id)),
            **overtime_data
        }
        results.append(result)
    
    return results


@router.get("/today", response_model=AttendanceResponse)
def get_today_attendance(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Get today's latest attendance record for current user"""
    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    attendance = db.query(Attendance).filter(
        Attendance.employee_id == current_user.id,
        Attendance.check_in_time >= today_start
    ).order_by(Attendance.check_in_time.desc()).first()  # Get most recent
    
    if not attendance:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No attendance record found for today"
        )
    
    return attendance


@router.get("/daily/status")
def get_daily_attendance_status(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Get today's attendance status with overtime calculations
    Returns check-in status, check-out status, working hours, and overtime details
    """
    from app.utils.overtime import (
        is_weekend, is_holiday,
        session_hours, split_day_hours,
    )

    # Close a session that has hit the cap before reporting on it, so the
    # employee sees the auto-checkout the moment they load the dashboard.
    close_expired_sessions(db, employee_id=current_user.id)

    today_start = _day_start()
    todays_sessions = day_sessions(db, current_user.id, today_start)
    attendance = todays_sessions[-1] if todays_sessions else None  # most recent check-in

    current_time = datetime.utcnow()
    today = today_start.date()

    if not attendance:
        return {
            "date": today.isoformat(),
            "has_checked_in": False,
            "has_checked_out": False,
            "check_in_time": None,
            "check_out_time": None,
            "working_hours": 0.0,
            "regular_hours": 0.0,
            "overtime_hours": 0.0,
            "overtime_bonus": 0.0,
            "overtime_rate": 0.0,
            "overtime_eligible": current_user.overtime_eligible,
            "status": "not_checked_in",
            "current_time": current_time.isoformat(),
            "message": "You haven't checked in today",
            "day_type": "weekend" if is_weekend(today) else "weekday"
        }
    
    has_checked_out = attendance.check_out_time is not None

    # Hours are totalled across every session of the day, not just the latest
    # one. The cap splits a long day into several rows, so measuring only the
    # most recent row would under-report both hours worked and overtime.
    cap = standard_hours_for(db, current_user.id)
    breakdown = split_day_hours(
        [session_hours(session, current_time) for session in todays_sessions],
        cap,
    )
    hours_calc = {
        "total_hours": round(sum(part["total_hours"] for part in breakdown), 2),
        "regular_hours": round(sum(part["regular_hours"] for part in breakdown), 2),
        # Time past the standard day. Not overtime — overtime is what has been
        # logged and signed off, which is read separately below.
        "extra_hours": round(sum(part["overtime_hours"] for part in breakdown), 2),
    }
    from app.utils.overtime import approved_overtime_by_day
    claimed_overtime = round(approved_overtime_by_day(
        db, todays_sessions).get((current_user.id, today), 0.0), 2)
    # A day with a session still running is not a finished day, however many
    # hours are already on it. The cap closes a long session at the standard
    # hours, so somebody who checks in again afterwards has a closed 8-hour
    # session and an open one — and reporting that as "completed 8.0h" told
    # them their day was over while they were sitting at their desk working.
    still_open = any(session.check_out_time is None for session in todays_sessions)
    day_completed = (hours_calc["total_hours"] + 0.001 >= cap) and not still_open
    auto_closed_today = any(is_auto_checkout(session) for session in todays_sessions)

    # Calculate overtime bonus
    overtime_bonus = 0.0
    overtime_rate = 0.0
    day_type = "weekday"
    
    # Deliberately left at zero — see the note above. Extra hours are shown,
    # the money is not, because the money does not exist until a claim is
    # approved twice.
    day_type = "holiday" if is_holiday(today) else ("weekend" if is_weekend(today) else "weekday")
    
    if has_checked_out:
        status = "completed"
        if is_auto_checkout(attendance):
            message = (
                f"You were checked out automatically at "
                f"{attendance.check_out_time.strftime('%I:%M %p')} after {cap:g} hours. "
                f"Check in again to record overtime."
            )
        else:
            message = f"You worked {hours_calc['total_hours']:.2f} hours today"
            if hours_calc['extra_hours'] > 0:
                # Said as a fact, not as a claim. Extra hours are only paid
                # once they are logged and approved, so the message points at
                # the step rather than implying the money is already coming.
                message += (f" — {hours_calc['extra_hours']:.2f}h past your standard "
                            f"day. Log it under Time Logs if you want it "
                            f"considered for overtime.")
    else:
        status = "checked_in"
        message = f"You are currently working. Duration: {hours_calc['total_hours']:.2f} hours"
        if hours_calc['extra_hours'] > 0:
            message += (f" — {hours_calc['extra_hours']:.2f}h past your standard day")

    return {
        "date": today.isoformat(),
        "has_checked_in": True,
        "has_checked_out": has_checked_out,
        "check_in_time": attendance.check_in_time.isoformat() if attendance.check_in_time else None,
        "check_out_time": attendance.check_out_time.isoformat() if attendance.check_out_time else None,
        "working_hours": hours_calc["total_hours"],
        "regular_hours": hours_calc["regular_hours"],
        "overtime_hours": claimed_overtime,
        "overtime_claimed": claimed_overtime > 0,
        "extra_hours": hours_calc["extra_hours"],
        "day_total_hours": hours_calc["total_hours"],
        "day_required_hours": round(cap, 2),
        "day_completed": day_completed,
        "overtime_bonus": overtime_bonus,
        "overtime_rate": overtime_rate,
        "overtime_eligible": current_user.overtime_eligible,
        "status": status,
        "current_time": current_time.isoformat(),
        "message": message,
        "attendance_id": attendance.id,
        "day_type": day_type,
        "sessions_today": len(todays_sessions),
        "session_cap_hours": cap,
        "auto_checked_out": auto_closed_today and has_checked_out,
    }


@router.get("/daily/report")
def get_daily_attendance_report(
    start_date: datetime = None,
    end_date: datetime = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Get daily attendance report for a date range
    Default: Last 30 days
    """
    close_expired_sessions(db, employee_id=current_user.id)

    if not end_date:
        end_date = datetime.utcnow()
    if not start_date:
        start_date = end_date - timedelta(days=30)

    attendances = db.query(Attendance).filter(
        Attendance.employee_id == current_user.id,
        Attendance.check_in_time >= start_date,
        Attendance.check_in_time <= end_date
    ).order_by(Attendance.check_in_time.desc()).all()
    
    total_days = len(attendances)
    total_hours = sum([a.duration_hours for a in attendances if a.duration_hours])
    completed_days = len([a for a in attendances if a.check_out_time])
    incomplete_days = total_days - completed_days
    
    from app.utils.overtime import day_context
    from app.utils.shifts import punctuality, shift_for

    # Completion is judged against the whole day, so the second session of a
    # capped day is not reported as a short one.
    day_hours = day_context(db, attendances)

    daily_records = []
    for attendance in attendances:
        day = attendance.check_in_time.date()
        hours_calc = day_hours.get(attendance.id)
        record = {
            "date": day.isoformat(),
            "check_in_time": attendance.check_in_time.isoformat(),
            "check_out_time": attendance.check_out_time.isoformat() if attendance.check_out_time else None,
            "working_hours": round(attendance.duration_hours, 2) if attendance.duration_hours else 0.0,
            "day_total_hours": (hours_calc or {}).get("day_total_hours"),
            "day_required_hours": (hours_calc or {}).get("day_required_hours"),
            "day_completed": (hours_calc or {}).get("day_completed"),
            "status": _row_status(attendance, hours_calc),
        }
        # Late and early-leaving are only answerable against a shift. Without
        # one the keys are simply absent, rather than defaulting to "on time"
        # for a company that has never defined when the day starts.
        shift = shift_for(db, current_user.id, day)
        if shift and shift.works_on(day):
            record["shift"] = punctuality(shift, attendance.check_in_time,
                                          attendance.check_out_time)
        daily_records.append(record)
    
    return {
        "summary": {
            "start_date": start_date.date().isoformat(),
            "end_date": end_date.date().isoformat(),
            "total_days_attended": total_days,
            "completed_days": completed_days,
            "incomplete_days": incomplete_days,
            "total_working_hours": round(total_hours, 2),
            "average_hours_per_day": round(total_hours / total_days, 2) if total_days > 0 else 0.0
        },
        "daily_records": daily_records
    }


@router.get("/weekly/overtime")
def get_weekly_overtime_report(
    week_start: datetime = None,
    week_end: datetime = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Get weekly overtime report
    Default: Current week (Monday to Sunday)
    """
    from app.utils.overtime import (
        get_week_start_end, is_weekend, get_day_name,
        approved_overtime_by_day, day_context,
        hourly_rate_from_salary, standard_hours_resolver,
    )

    close_expired_sessions(db, employee_id=current_user.id)

    # Get week boundaries
    if not week_start or not week_end:
        week_start, week_end = get_week_start_end()

    # Get all attendance for the week
    attendances = db.query(Attendance).filter(
        Attendance.employee_id == current_user.id,
        Attendance.check_in_time >= week_start,
        Attendance.check_in_time <= week_end
    ).order_by(Attendance.check_in_time).all()

    # Hours belong to the day, not the row: a day made of an 8h session plus a
    # 3h evening session is one eleven-hour day. What counts as overtime in it
    # is whatever has been claimed and signed off, not whatever the clock ran
    # past — the rest is reported as extra hours and carries no money.
    day_hours = day_context(db, attendances, standard_hours_resolver(db))
    claimed_overtime = approved_overtime_by_day(db, attendances)

    # Calculate totals
    total_working_hours = 0.0
    total_regular_hours = 0.0
    total_overtime_hours = 0.0
    total_extra_hours = 0.0
    total_overtime_bonus = 0.0

    daily_breakdown = []
    counted_days = set()

    for attendance in attendances:
        if attendance.check_out_time:
            day = attendance.check_in_time.date()
            hours_calc = day_hours[attendance.id]

            total_working_hours += hours_calc["total_hours"]
            total_regular_hours += hours_calc["regular_hours"]
            total_extra_hours += hours_calc["extra_hours"]
            # Claimed overtime is a figure for the whole day, so it is added
            # once however many sessions that day was stored as.
            claimed = claimed_overtime.get((attendance.employee_id, day), 0.0)
            if day not in counted_days:
                total_overtime_hours += claimed
                counted_days.add(day)
            # No money against any of it — overtime is paid only once claimed
            # through /time-logs and approved by the line manager and HR.

            daily_breakdown.append({
                "date": day.isoformat(),
                "day_name": get_day_name(day),
                "check_in_time": attendance.check_in_time.isoformat(),
                "check_out_time": attendance.check_out_time.isoformat(),
                "working_hours": hours_calc["total_hours"],
                "regular_hours": hours_calc["regular_hours"],
                "overtime_hours": round(claimed, 2),
                "overtime_claimed": claimed > 0,
                "extra_hours": hours_calc["extra_hours"],
                "day_total_hours": hours_calc["day_total_hours"],
                "day_required_hours": hours_calc["day_required_hours"],
                "day_completed": hours_calc["day_completed"],
                "overtime_bonus": 0.0,
                "overtime_rate": 0.0,
                "status": _row_status(attendance, hours_calc),
                "day_type": day_type_for(day),
            })
    
    return {
        "week_start": week_start.date().isoformat(),
        "week_end": week_end.date().isoformat(),
        "total_working_hours": round(total_working_hours, 2),
        "total_regular_hours": round(total_regular_hours, 2),
        "total_overtime_hours": round(total_overtime_hours, 2),
        "total_extra_hours": round(total_extra_hours, 2),
        "estimated_overtime_bonus": round(total_overtime_bonus, 2),
        "currency": settings.OVERTIME_BONUS_CURRENCY,
        "days_worked": len(attendances),
        "daily_breakdown": daily_breakdown,
        "employee": {
            "id": current_user.id,
            "name": current_user.full_name,
            "hourly_rate": hourly_rate_from_salary(current_user.base_salary),
            "overtime_eligible": current_user.overtime_eligible
        }
    }


@router.get("/monthly/overtime")
def get_monthly_overtime_summary(
    month: int = None,
    year: int = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Get monthly overtime summary
    Default: Current month
    """
    from app.utils.overtime import (
        is_weekend, is_holiday,
        hourly_rate_from_salary, hours_by_attendance_id, standard_hours_resolver,
    )
    import calendar

    close_expired_sessions(db, employee_id=current_user.id)

    current_date = datetime.utcnow()
    if month is None:
        month = current_date.month
    if year is None:
        year = current_date.year
    
    # Get month boundaries
    month_start = datetime(year, month, 1)
    last_day = calendar.monthrange(year, month)[1]
    month_end = datetime(year, month, last_day, 23, 59, 59)
    
    # Get all attendance for the month
    attendances = db.query(Attendance).filter(
        Attendance.employee_id == current_user.id,
        Attendance.check_in_time >= month_start,
        Attendance.check_in_time <= month_end,
        Attendance.check_out_time != None  # Only completed days
    ).all()
    
    # Overtime is split per employee-day so sessions created by the cap add up
    # correctly instead of each reporting zero overtime.
    day_hours = hours_by_attendance_id(attendances, standard_hours_resolver(db))

    # Calculate totals — distinct calendar days worked, since the cap can store
    # one day as several rows.
    total_days_worked = len({a.check_in_time.date() for a in attendances})
    total_working_hours = 0.0
    total_regular_hours = 0.0
    total_overtime_hours = 0.0
    total_overtime_bonus = 0.0
    weekday_overtime = 0.0
    weekend_overtime = 0.0
    holiday_overtime = 0.0

    for attendance in attendances:
        hours_calc = day_hours[attendance.id]

        total_working_hours += hours_calc["total_hours"]
        total_regular_hours += hours_calc["regular_hours"]
        total_overtime_hours += hours_calc["overtime_hours"]
        # total_overtime_bonus stays zero: extra hours are a fact about the
        # month, pay is not, and only an approved claim creates the latter.

        # Categorise the extra hours by day type, which is still useful for
        # deciding whether a claim is reasonable.
        day_type = day_type_for(attendance.check_in_time.date())
        if day_type == "holiday":
            holiday_overtime += hours_calc["overtime_hours"]
        elif day_type == "weekend":
            weekend_overtime += hours_calc["overtime_hours"]
        else:
            weekday_overtime += hours_calc["overtime_hours"]
    
    month_name = calendar.month_name[month]
    average_daily_hours = total_working_hours / total_days_worked if total_days_worked > 0 else 0.0
    
    return {
        "month": month,
        "year": year,
        "month_name": month_name,
        "total_days_worked": total_days_worked,
        "total_working_hours": round(total_working_hours, 2),
        "total_regular_hours": round(total_regular_hours, 2),
        "total_overtime_hours": round(total_overtime_hours, 2),
        "total_overtime_bonus": round(total_overtime_bonus, 2),
        "currency": settings.OVERTIME_BONUS_CURRENCY,
        "average_daily_hours": round(average_daily_hours, 2),
        "overtime_breakdown": {
            "weekday_overtime": round(weekday_overtime, 2),
            "weekend_overtime": round(weekend_overtime, 2),
            "holiday_overtime": round(holiday_overtime, 2)
        },
        "employee": {
            "id": current_user.id,
            "name": current_user.full_name,
            "hourly_rate": hourly_rate_from_salary(current_user.base_salary),
            "overtime_eligible": current_user.overtime_eligible,
            "standard_hours": current_user.standard_hours
        }
    }


@router.get("/team/today")
def get_team_today_status(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_manager)
):
    """
    Today's status for everyone the caller may see, in one request.

    The team view used to ask for each person separately. Twenty people meant
    twenty requests, each one authenticating, opening a session and running
    its own queries — and because they were sent at once they raced each
    other for the connection pool, so a handful came back as 503s and those
    rows showed the wrong status. The work is the same work; it just belongs
    in one answer.

    The shape of each entry is identical to /employee/{id}/today, so the two
    can be read by the same code.
    """
    if is_privileged(current_user, db):
        team = (db.query(User).filter(User.is_active.is_(True))
                .order_by(User.full_name).all())
    else:
        team = (db.query(User)
                .filter(User.manager_id == current_user.id, User.is_active.is_(True))
                .order_by(User.full_name).all())
    if not team:
        return {"date": _day_start().date().isoformat(), "employees": []}

    # Sweep every capped session once rather than once per person.
    close_expired_sessions(db)

    ids = [u.id for u in team]
    day_start = _day_start()
    rows = (db.query(Attendance)
            .filter(Attendance.employee_id.in_(ids),
                    Attendance.check_in_time >= day_start,
                    Attendance.check_in_time < day_start + timedelta(days=1))
            .order_by(Attendance.check_in_time)
            .all())
    by_employee: dict = {}
    for row in rows:
        by_employee.setdefault(row.employee_id, []).append(row)

    out = []
    for employee in team:
        sessions = by_employee.get(employee.id, [])
        who = {"id": employee.id, "name": employee.full_name,
               "email": employee.email, "designation": employee.designation}
        if not sessions:
            out.append({
                "employee": who, "status": "not_checked_in",
                "message": "Not checked in today",
                "check_in_time": None, "check_out_time": None,
                "working_hours": None, "is_present": False,
            })
            continue
        latest = sessions[-1]
        completed = sum(s.duration_hours or 0.0 for s in sessions if s.check_out_time)
        worked = round(completed, 2) if completed else None
        out.append({
            "employee": who,
            "status": "checked_out" if latest.check_out_time else "checked_in",
            "message": (f"Worked {worked} hours" if latest.check_out_time
                        else f"Checked in at {latest.check_in_time.strftime('%I:%M %p')}"),
            "check_in_time": latest.check_in_time.isoformat(),
            "check_out_time": (latest.check_out_time.isoformat()
                               if latest.check_out_time else None),
            "working_hours": worked,
            "is_present": True,
            "sessions_today": len(sessions),
            "auto_checked_out": is_auto_checkout(latest),
            "location": {"check_in_address": latest.check_in_address,
                         "check_out_address": latest.check_out_address},
        })
    return {"date": day_start.date().isoformat(), "employees": out}


@router.get("/employee/{employee_id}/today")
def get_employee_today_status(
    employee_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_manager)
):
    """
    Get today's attendance status for a specific employee (Manager/Admin only)
    Perfect for team view - shows current check-in/out status
    """
    # Check if manager has authority
    if not is_privileged(current_user):
        employee = db.query(User).filter(User.id == employee_id).first()
        if not employee or employee.manager_id != current_user.id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You can only view attendance of your subordinates"
            )
    
    # Get employee info
    employee = db.query(User).filter(User.id == employee_id).first()
    if not employee:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Employee not found"
        )
    
    # Close a capped session before reporting, otherwise the team view shows
    # someone as still working hours after their day ended.
    close_expired_sessions(db, employee_id=employee_id)

    # Get today's attendance (most recent)
    today_start = _day_start()
    todays_sessions = day_sessions(db, employee_id, today_start)
    attendance = todays_sessions[-1] if todays_sessions else None

    if not attendance:
        return {
            "employee": {
                "id": employee.id,
                "name": employee.full_name,
                "email": employee.email,
                "designation": employee.designation
            },
            "status": "not_checked_in",
            "message": "Not checked in today",
            "check_in_time": None,
            "check_out_time": None,
            "working_hours": None,
            "is_present": False
        }
    
    # Working hours cover the whole day, so a day split by the cap still reports
    # the full time worked rather than just the last session.
    completed_hours = sum(
        session.duration_hours or 0.0
        for session in todays_sessions
        if session.check_out_time
    )
    working_hours = round(completed_hours, 2) if completed_hours else None

    status = "checked_out" if attendance.check_out_time else "checked_in"

    return {
        "employee": {
            "id": employee.id,
            "name": employee.full_name,
            "email": employee.email,
            "designation": employee.designation
        },
        "status": status,
        "message": f"Checked in at {attendance.check_in_time.strftime('%I:%M %p')}" if not attendance.check_out_time else f"Worked {working_hours} hours",
        "check_in_time": attendance.check_in_time.isoformat(),
        "check_out_time": attendance.check_out_time.isoformat() if attendance.check_out_time else None,
        "working_hours": working_hours,
        "is_present": True,
        "sessions_today": len(todays_sessions),
        "auto_checked_out": is_auto_checkout(attendance),
        "location": {
            "check_in_address": attendance.check_in_address,
            "check_out_address": attendance.check_out_address
        }
    }


@router.get("/employee/{employee_id}/week")
def get_employee_week_summary(
    employee_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_manager)
):
    """
    Get this week's attendance summary for a specific employee (Manager/Admin only)
    Shows days worked, total hours, average hours
    """
    from app.utils.overtime import get_week_start_end

    # Check if manager has authority
    if not is_privileged(current_user):
        employee = db.query(User).filter(User.id == employee_id).first()
        if not employee or employee.manager_id != current_user.id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You can only view attendance of your subordinates"
            )

    close_expired_sessions(db, employee_id=employee_id)

    # Get employee info
    employee = db.query(User).filter(User.id == employee_id).first()
    if not employee:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Employee not found"
        )
    
    # Get week boundaries
    week_start, week_end = get_week_start_end()
    
    # Get this week's attendance
    attendances = db.query(Attendance).filter(
        Attendance.employee_id == employee_id,
        Attendance.check_in_time >= week_start,
        Attendance.check_in_time <= week_end
    ).order_by(Attendance.check_in_time).all()
    
    total_hours = 0.0
    days_worked = 0
    completed_days = 0
    
    daily_records = []
    for attendance in attendances:
        if attendance.check_out_time:
            delta = attendance.check_out_time - attendance.check_in_time
            hours = delta.total_seconds() / 3600
            total_hours += hours
            completed_days += 1
            
            daily_records.append({
                "date": attendance.check_in_time.date().isoformat(),
                "check_in": attendance.check_in_time.strftime('%I:%M %p'),
                "check_out": attendance.check_out_time.strftime('%I:%M %p'),
                "hours": round(hours, 2),
                "status": "completed"
            })
        else:
            daily_records.append({
                "date": attendance.check_in_time.date().isoformat(),
                "check_in": attendance.check_in_time.strftime('%I:%M %p'),
                "check_out": None,
                "hours": None,
                "status": "in_progress"
            })
        days_worked += 1
    
    avg_hours = round(total_hours / completed_days, 2) if completed_days > 0 else 0
    
    return {
        "employee": {
            "id": employee.id,
            "name": employee.full_name,
            "email": employee.email,
            "designation": employee.designation
        },
        "week_period": {
            "start": week_start.date().isoformat(),
            "end": week_end.date().isoformat()
        },
        "summary": {
            "days_worked": days_worked,
            "days_completed": completed_days,
            "total_hours": round(total_hours, 2),
            "average_hours_per_day": avg_hours
        },
        "daily_records": daily_records
    }


@router.get("/employee/{employee_id}", response_model=List[AttendanceResponse])
def get_employee_attendance(
    employee_id: int,
    start_date: datetime = None,
    end_date: datetime = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_manager)
):
    """Get attendance records for a specific employee (Manager/Admin only)"""
    # Check if manager has authority
    if not is_privileged(current_user):
        employee = db.query(User).filter(User.id == employee_id).first()
        if not employee or employee.manager_id != current_user.id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You can only view attendance of your subordinates"
            )
    
    query = db.query(Attendance).filter(Attendance.employee_id == employee_id)
    
    if start_date:
        query = query.filter(Attendance.check_in_time >= start_date)
    if end_date:
        query = query.filter(Attendance.check_in_time <= end_date)
    
    attendances = query.order_by(Attendance.check_in_time.desc()).all()
    return attendances


@router.get("/{attendance_id}", response_model=AttendanceResponse)
def get_attendance(
    attendance_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Get attendance record by ID"""
    attendance = db.query(Attendance).filter(Attendance.id == attendance_id).first()
    
    if not attendance:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Attendance record not found"
        )
    
    # Check permissions
    if (is_privileged(current_user) or 
        attendance.employee_id == current_user.id or
        (current_user.role == RoleEnum.MANAGER and attendance.employee.manager_id == current_user.id)):
        return attendance
    else:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not enough permissions"
        )

