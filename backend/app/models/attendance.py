from sqlalchemy import Column, Integer, Float, ForeignKey, DateTime, String
from sqlalchemy.orm import relationship
from datetime import datetime
from app.database import Base


class Attendance(Base):
    __tablename__ = "attendances"
    
    id = Column(Integer, primary_key=True, index=True)
    employee_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    
    # Check-in details
    check_in_time = Column(DateTime, nullable=False)
    check_in_latitude = Column(Float)
    check_in_longitude = Column(Float)
    check_in_address = Column(String)
    
    # Check-out details
    check_out_time = Column(DateTime, nullable=True)
    check_out_latitude = Column(Float, nullable=True)
    check_out_longitude = Column(Float, nullable=True)
    check_out_address = Column(String, nullable=True)
    
    # Duration in hours
    duration_hours = Column(Float, nullable=True)

    # Which signal placed this check-in: "gps" when the coordinates fell
    # inside the geofence, "network" when the request came from the office
    # connection without a usable fix, "remote" for someone exempt from the
    # fence. Null on every row written before this was recorded. Kept because
    # the two are not equally strong evidence — coordinates are typed in by
    # the client, a source address is assigned by the network — and an audit
    # six months from now cannot tell them apart otherwise.
    verified_by = Column(String, nullable=True)
    
    # Notes
    notes = Column(String)
    # Where this session came from: "portal" when somebody pressed check in,
    # "device" when it was raised from a fingerprint scan. Kept so a session
    # nobody typed is distinguishable — on an approval screen, in an audit, and
    # to the code that must never raise a second one for the same day.
    source = Column(String, nullable=False, server_default="portal", default="portal")
    
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    employee = relationship("User", back_populates="attendances")
    
    def __repr__(self):
        return f"<Attendance {self.check_in_time} for employee_id={self.employee_id}>"

