"""
The office fingerprint device, reconciled against portal attendance.

The device knows people by short enrolment names — "Agha", "Qalb", "hamzaM" —
which are neither unique nor stable: someone re-enrolled gets a second name and
their history splits in two. So identities are mapped explicitly rather than
matched by similarity. A wrong guess here credits one person's hours to
another, on a system that feeds payroll.
"""
from sqlalchemy import (Column, Integer, String, Text, Date, DateTime, Boolean,
                        ForeignKey, UniqueConstraint, Index)
from sqlalchemy.orm import relationship
from datetime import datetime

from app.database import Base


class BiometricIdentity(Base):
    """
    One enrolment name on the device, and who it belongs to.

    Three states, and the difference matters: mapped to an employee, explicitly
    ignored (a leaver or a contractor), or neither — which means nobody has
    decided yet, and the sync should say so rather than quietly drop the scans.
    """
    __tablename__ = "biometric_identities"

    id = Column(Integer, primary_key=True, index=True)
    device_name = Column(String, nullable=False, unique=True, index=True)
    employee_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)

    # Deliberately excluded: a former employee, or somebody who is not on the
    # HRM at all. Distinct from "not yet decided".
    ignored = Column(Boolean, default=False, nullable=False)
    note = Column(Text, nullable=True)

    first_seen = Column(Date, nullable=True)
    last_seen = Column(Date, nullable=True)
    scan_count = Column(Integer, default=0, nullable=False)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    employee = relationship("User", foreign_keys=[employee_id])

    @property
    def is_resolved(self) -> bool:
        return self.employee_id is not None or self.ignored


class BiometricScan(Base):
    """
    One person's badge record for one day, as the device reports it.

    The device gives the day's first and last scan, so when they are equal the
    person badged once and did not badge out — that is a fact about the day,
    not a zero-length shift, and it is stored as-is rather than being turned
    into a duration.

    Times are converted to UTC on the way in. The device reports local Pakistan
    time; everything else in this system stores UTC, and mixing the two silently
    shifts every record by five hours.
    """
    __tablename__ = "biometric_scans"

    id = Column(Integer, primary_key=True, index=True)
    device_name = Column(String, nullable=False, index=True)
    employee_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)

    scan_date = Column(Date, nullable=False, index=True)
    first_scan = Column(DateTime, nullable=False)     # UTC
    last_scan = Column(DateTime, nullable=False)      # UTC
    # True when first and last are the same moment — one scan, no badge out.
    single_scan = Column(Boolean, default=False, nullable=False)

    synced_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    employee = relationship("User", foreign_keys=[employee_id])

    __table_args__ = (
        # The device is polled repeatedly and re-reports the same day, so a
        # sync must update rather than accumulate duplicates.
        UniqueConstraint("device_name", "scan_date", name="uq_biometric_name_date"),
    )


Index("ix_biometric_scans_employee_date", BiometricScan.employee_id, BiometricScan.scan_date)
