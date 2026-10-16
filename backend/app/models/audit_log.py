from sqlalchemy import Column, Integer, String, DateTime, Text, ForeignKey, Index
from datetime import datetime
from app.database import Base


class AuditLog(Base):
    """
    Append-only record of security-relevant actions.

    Written for anything that changes who someone is or what they are owed —
    role and salary edits, leave decisions, password resets, account removal —
    plus every login attempt. Nothing here is ever updated or deleted: if a
    record was altered, this is the only place that says who did it.

    `actor_id` is nullable because a failed login has no authenticated actor
    yet; `actor_email` preserves what was attempted in that case.
    """
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True, index=True)

    # Who acted. actor_id is NULL for anonymous/failed attempts.
    actor_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    actor_email = Column(String, nullable=True)
    ip_address = Column(String, nullable=True)

    # What happened: a stable machine-readable verb, e.g. "login.failed",
    # "user.role_changed", "leave.approved".
    action = Column(String, nullable=False, index=True)

    # What it happened to.
    target_type = Column(String, nullable=True)   # "user", "leave", ...
    target_id = Column(Integer, nullable=True)

    # Free-form detail: old/new values, reason, request context.
    detail = Column(Text, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)


# Failed-login lockout counts recent rows by action+email; keep that cheap.
Index("ix_audit_logs_action_email_time", AuditLog.action, AuditLog.actor_email, AuditLog.created_at)
