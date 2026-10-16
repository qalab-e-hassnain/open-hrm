from sqlalchemy import Column, Integer, Float, ForeignKey, DateTime, String, Enum, UniqueConstraint
from sqlalchemy.orm import relationship
from sqlalchemy.ext.hybrid import hybrid_property
from datetime import datetime
import enum
from app.database import Base


class PayrollRunStatusEnum(str, enum.Enum):
    DRAFT = "draft"
    FINALIZED = "finalized"
    PAID = "paid"


class PayrollRun(Base):
    """One monthly payroll sheet (e.g. "2025-11") covering every employee."""
    __tablename__ = "payroll_runs"

    id = Column(Integer, primary_key=True, index=True)
    month = Column(String, unique=True, index=True, nullable=False)  # "YYYY-MM"
    status = Column(Enum(PayrollRunStatusEnum), default=PayrollRunStatusEnum.DRAFT, nullable=False)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    finalized_at = Column(DateTime, nullable=True)

    creator = relationship("User", foreign_keys=[created_by])
    entries = relationship(
        "PayrollEntry",
        back_populates="run",
        cascade="all, delete-orphan",
    )

    def __repr__(self):
        return f"<PayrollRun {self.month} ({self.status})>"


class PayrollEntry(Base):
    """One employee's row on a payroll sheet."""
    __tablename__ = "payroll_entries"
    __table_args__ = (
        # An employee can only appear once per run.
        UniqueConstraint("payroll_run_id", "employee_id", name="uq_payroll_entry_run_employee"),
    )

    id = Column(Integer, primary_key=True, index=True)
    payroll_run_id = Column(Integer, ForeignKey("payroll_runs.id"), nullable=False, index=True)
    employee_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)

    # Snapshot of User.base_salary taken when the entry was created. Never
    # re-read from the user afterwards — a later raise must not rewrite history.
    monthly_salary = Column(Float, nullable=False, default=0.0)

    # Adjustable columns (the only ones PATCH may touch).
    increment = Column(Float, default=0.0, nullable=False)
    allowance_overtime = Column(Float, default=0.0, nullable=False)
    arrears = Column(Float, default=0.0, nullable=False)
    bonus = Column(Float, default=0.0, nullable=False)
    tax_deduction = Column(Float, default=0.0, nullable=False)
    other_deduction = Column(Float, default=0.0, nullable=False)
    notes = Column(String, nullable=True)

    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    run = relationship("PayrollRun", back_populates="entries")
    employee = relationship("User", foreign_keys=[employee_id])
    audit_logs = relationship(
        "PayrollAuditLog",
        back_populates="entry",
        cascade="all, delete-orphan",
    )

    # --- Computed columns: never stored, always derived. ---
    # hybrid_property so they work both on a loaded instance (Python) and
    # inside a SQL expression (e.g. ordering/aggregating in a query).
    @hybrid_property
    def gross_salary(self) -> float:
        return (self.monthly_salary or 0.0) + (self.increment or 0.0)

    @hybrid_property
    def gross_total(self) -> float:
        return (
            self.gross_salary
            + (self.allowance_overtime or 0.0)
            + (self.arrears or 0.0)
            + (self.bonus or 0.0)
        )

    @hybrid_property
    def net_payable(self) -> float:
        return self.gross_total - (self.tax_deduction or 0.0) - (self.other_deduction or 0.0)

    def __repr__(self):
        return f"<PayrollEntry run={self.payroll_run_id} employee={self.employee_id}>"


class PayrollAuditLog(Base):
    """One row per field changed on a payroll entry — the audit trail.

    old_value/new_value are floats and cover the numeric adjustable columns.
    Two cases don't fit those columns, hence the extra nullable fields:
      - run-level events (finalize/reopen) have no entry -> payroll_entry_id is
        NULL and payroll_run_id carries the link;
      - text changes (notes) and the reopen reason are recorded in `note`.
    """
    __tablename__ = "payroll_audit_log"

    id = Column(Integer, primary_key=True, index=True)
    payroll_entry_id = Column(Integer, ForeignKey("payroll_entries.id"), nullable=True, index=True)
    payroll_run_id = Column(Integer, ForeignKey("payroll_runs.id"), nullable=True, index=True)
    field_changed = Column(String, nullable=False)
    old_value = Column(Float, nullable=True)
    new_value = Column(Float, nullable=True)
    note = Column(String, nullable=True)
    changed_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    changed_at = Column(DateTime, default=datetime.utcnow)

    entry = relationship("PayrollEntry", back_populates="audit_logs")
    changer = relationship("User", foreign_keys=[changed_by])

    def __repr__(self):
        return f"<PayrollAuditLog {self.field_changed} on entry={self.payroll_entry_id}>"
