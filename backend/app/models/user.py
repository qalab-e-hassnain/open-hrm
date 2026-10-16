from sqlalchemy import Column, Integer, String, Enum, ForeignKey, Date, DateTime, Boolean, Float, event
from sqlalchemy.orm import relationship
from datetime import datetime
import enum
from app.database import Base


class RoleEnum(str, enum.Enum):
    EMPLOYEE = "employee"
    MANAGER = "manager"
    ADMIN = "admin"


class GenderEnum(str, enum.Enum):
    MALE = "male"
    FEMALE = "female"
    OTHER = "other"


class User(Base):
    __tablename__ = "users"
    
    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=False)
    full_name = Column(String, nullable=False)
    role = Column(Enum(RoleEnum), default=RoleEnum.EMPLOYEE, nullable=False)
    # Nullable for backward compatibility with users created before this field
    # existed; new users are required to set it (see UserCreate). Drives which
    # of maternity/paternity leave a user is offered.
    gender = Column(Enum(GenderEnum), nullable=True)

    # An HR manager carries the same reach as an admin (see deps.is_privileged)
    # with one hard limit: they can never act on an admin account, nor grant the
    # admin role. A flag rather than a role value, so it can be revoked.
    is_hr = Column(Boolean, default=False, nullable=False, server_default="false")

    # Owns the lab: counts, labels, receiving and audits. A flag rather than a
    # role for the same reason is_hr is one — it can be revoked without
    # rebuilding somebody's account, and the inventory module needs no
    # permission system of its own. An admin reaches everything a custodian
    # does without carrying the flag (see deps.is_custodian).
    is_lab_custodian = Column(Boolean, default=False, nullable=False,
                              server_default="false")

    # Works away from the office. The geofence is skipped for this person
    # alone, rather than being loosened for everybody — a remote employee
    # otherwise cannot check in at all, since check-in refuses any location
    # beyond the office radius.
    works_remotely = Column(Boolean, default=False, nullable=False, server_default="false")

    # On payroll but not expected to attend — a director, or anyone whose
    # employment does not involve turning up. Without this they appear absent
    # every day for ever in attendance reporting.
    attendance_exempt = Column(Boolean, default=False, nullable=False, server_default="false")

    @property
    def profile_completion(self) -> dict:
        """
        What is still missing from this record.

        A property rather than something the endpoints assemble, so it rides
        along on every UserResponse — including the one embedded in the login
        payload, which is what lets the frontend ask somebody to finish their
        profile the moment they sign in.
        """
        from app.utils.profile_completion import evaluate
        return evaluate(self)

    @property
    def has_hr_access(self) -> bool:
        """
        Whether this person reaches the HR area at all.

        An admin outranks HR everywhere in this codebase, so gating an HR
        screen on the is_hr flag alone hides it from the very people who
        already have every permission behind it. Exposed as one field so the
        client does not recombine role and is_hr and get it subtly wrong.
        """
        return bool(self.is_hr) or self.role == RoleEnum.ADMIN

    # Personal Information
    phone = Column(String)
    address = Column(String)
    date_of_birth = Column(DateTime)
    # "single" | "married". Drives whether maternity/paternity leave is offered
    # at all — the gender then decides which of the two.
    marital_status = Column(String, nullable=True)
    # Wedding anniversary, only meaningful when married.
    anniversary_date = Column(DateTime, nullable=True)
    # Year admins/HR were last told about this person's birthday / anniversary.
    # There is no scheduler in this stack, so the greeting sweep runs lazily on
    # notification reads and these markers keep it to once a year.
    birthday_notice_year = Column(Integer, nullable=True)
    anniversary_notice_year = Column(Integer, nullable=True)
    cnic_number = Column(String, nullable=True)  # CNIC (National ID) number
    father_name = Column(String, nullable=True)
    # Distinct from `email` (the work/login address) — used for personal
    # correspondence, not authentication.
    personal_email = Column(String, nullable=True)
    date_of_joining = Column(DateTime, default=datetime.utcnow)
    # Probation as a date somebody owns rather than an inference from joining.
    # Null means "never set" — the leave rules still fall back to the policy
    # window from the joining date, so nothing breaks for existing employees.
    probation_end_date = Column(Date, nullable=True)
    probation_status = Column(String, nullable=True)   # ProbationStatusEnum value
    # So the reminder sweep tells a manager once per probation period rather
    # than every day for the fortnight before it ends. Cleared on extension,
    # because an extended probation is a new period with a new deadline.
    probation_notice_sent_at = Column(DateTime, nullable=True)
    employee_id = Column(String, unique=True, index=True)
    department = Column(String, nullable=True)  # Department name
    designation = Column(String, nullable=True)  # Job title/designation
    
    # Overtime & Salary
    # base_salary is the employee's current monthly salary. Payroll runs take a
    # SNAPSHOT of it into PayrollEntry.monthly_salary at run-creation time, so
    # later raises never rewrite a historical sheet.
    base_salary = Column(Float, default=0.0)
    # Recurring monthly allowance. Prefills payslip generation so an admin
    # doesn't retype it each period.
    allowance = Column(Float, default=0.0)
    hourly_rate = Column(Float, default=0.0)
    overtime_eligible = Column(Boolean, default=True)
    standard_hours = Column(Float, default=8.0)
    
    # Emergency Contact
    emergency_contact_name = Column(String)
    emergency_contact_phone = Column(String)
    
    # Documents & Pictures
    profile_picture_url = Column(String, nullable=True)  # Profile picture with blue background, front facing
    linkedin_welcome_picture_url = Column(String, nullable=True)  # Picture for LinkedIn welcome post
    cnic_front_url = Column(String, nullable=True)  # CNIC (National ID) front side
    cnic_back_url = Column(String, nullable=True)  # CNIC (National ID) back side
    
    # Reporting Structure
    manager_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    
    # Status
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    manager = relationship("User", remote_side=[id], backref="subordinates")
    teams = relationship("UserTeam", back_populates="user")
    leaves = relationship("Leave", back_populates="employee", foreign_keys="Leave.employee_id")
    reimbursements = relationship("Reimbursement", back_populates="employee", foreign_keys="Reimbursement.employee_id")
    payrolls = relationship("Payroll", back_populates="employee")
    attendances = relationship("Attendance", back_populates="employee")
    leave_balance = relationship("LeaveBalance", back_populates="employee", uselist=False)
    notifications = relationship("Notification", back_populates="user")
    
    # Unguessable code printed on an employment letter so a bank or landlord
    # can confirm the letter is genuine without an HRM account. Per employee
    # rather than per letter: a letter states current employment, so a reissue
    # should verify against the same record rather than mint a second truth.
    employment_verification_code = Column(String, unique=True, index=True, nullable=True)
    # Last time this person was nudged to finish their record. Cleared once the
    # record is complete, so a field emptied later starts the cycle again.
    profile_reminder_sent_at = Column(DateTime, nullable=True)

    # Banking information
    account_number = Column(String, nullable=True)
    bank_number = Column(String, nullable=True)
    branch_code = Column(String, nullable=True)
    account_title = Column(String, nullable=True)

    @property
    def is_acting_admin(self) -> bool:
        """
        Read-only and session-derived (not a column): True while this user is
        covering full admin authority for an admin currently on approved
        leave — see app.api.deps.is_acting_admin. Computed here, on the model
        itself, so every UserResponse built anywhere in the app (login, /me,
        user lists, ...) reports it correctly without each call site having
        to remember to check. Returns False rather than raising if the
        instance isn't attached to a session.
        """
        from sqlalchemy.orm import object_session
        session = object_session(self)
        if session is None:
            return False
        from app.api.deps import is_acting_admin as _is_acting_admin
        return _is_acting_admin(self, session)

    def __repr__(self):
        return f"<User {self.email} ({self.role})>"


# The columns that hold a link to a private file. A signature belongs on the
# way out, never in the column.
_FILE_URL_COLUMNS = ("profile_picture_url", "linkedin_welcome_picture_url",
                     "cnic_front_url", "cnic_back_url")


@event.listens_for(User, "before_insert")
@event.listens_for(User, "before_update")
def _strip_stored_signatures(mapper, connection, target: "User") -> None:
    """
    Never store a signed URL.

    Signatures last an hour and are minted per response. A client that reads a
    record and saves it back hands the signature straight into the column, and
    from then on every response serves that same expiring signature — the
    picture works for an hour and is broken forever after. It happened to four
    profile pictures before this existed, so the rule is enforced here rather
    than at each of the half-dozen places that can write one.
    """
    from app.utils.signed_urls import strip_signature

    for column in _FILE_URL_COLUMNS:
        current = getattr(target, column, None)
        if current:
            cleaned = strip_signature(current)
            if cleaned != current:
                setattr(target, column, cleaned)


@event.listens_for(User, "before_insert")
@event.listens_for(User, "before_update")
def _derive_hourly_rate(mapper, connection, target: "User") -> None:
    """
    hourly_rate is derived, never typed in: monthly basic / 160 working hours.

    Written here rather than in each endpoint so it is impossible to save a
    salary without the matching rate — a stale rate would misprice overtime,
    which is what happened while the column was maintained by hand.
    """
    from app.utils.overtime import hourly_rate_from_salary

    target.hourly_rate = hourly_rate_from_salary(target.base_salary)

