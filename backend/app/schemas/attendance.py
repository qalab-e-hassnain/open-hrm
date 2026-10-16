from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime


# Check-in Schema
class AttendanceCheckIn(BaseModel):
    # Optional, because a desk PC has no GPS: navigator.geolocation answers
    # POSITION_UNAVAILABLE and the portal sends the check-in with no
    # coordinates at all. Required, these produced a 422 naming the missing
    # field, which is how somebody sitting at their own desk was told they
    # were not in the office. Where there is no fix, the source address
    # decides instead — see check_in().
    latitude: Optional[float] = Field(None, ge=-90, le=90)
    longitude: Optional[float] = Field(None, ge=-180, le=180)
    address: Optional[str] = None


# Check-out Schema
class AttendanceCheckOut(BaseModel):
    # Same reason as above, and the same machine: somebody who checked in at a
    # GPS-less desk has to be able to check out from it.
    latitude: Optional[float] = Field(None, ge=-90, le=90)
    longitude: Optional[float] = Field(None, ge=-180, le=180)
    address: Optional[str] = None


# Attendance Response Schema
class AttendanceResponse(BaseModel):
    id: int
    employee_id: int
    check_in_time: datetime
    check_in_latitude: Optional[float] = None
    check_in_longitude: Optional[float] = None
    check_in_address: Optional[str] = None
    check_out_time: Optional[datetime] = None
    check_out_latitude: Optional[float] = None
    check_out_longitude: Optional[float] = None
    check_out_address: Optional[str] = None
    duration_hours: Optional[float] = None
    # "gps", "network" or "remote" — which signal placed this check-in.
    verified_by: Optional[str] = None
    notes: Optional[str] = None
    created_at: datetime
    
    class Config:
        from_attributes = True

