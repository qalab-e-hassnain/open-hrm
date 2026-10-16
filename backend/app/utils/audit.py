"""Append-only audit trail and the failed-login lockout built on top of it."""

import json
import logging
from datetime import datetime, timedelta
from typing import Any, Optional

from fastapi import Request
from sqlalchemy.orm import Session

from app.models.audit_log import AuditLog

logger = logging.getLogger(__name__)

# Failed-login policy. Counted per email address over a rolling window, so a
# password-spray against one account locks that account rather than the whole
# site, and a legitimate user's own mistakes expire on their own.
MAX_FAILED_LOGINS = 5
LOCKOUT_WINDOW = timedelta(minutes=15)


def client_ip(request: Optional[Request]) -> Optional[str]:
    """
    The caller's address as seen from outside.

    Behind Azure App Service the socket peer is the platform's front end, so
    prefer X-Forwarded-For's first entry — the original client. Only trust it
    because nothing else is reachable from the internet; a direct-to-container
    deployment would need this narrowed.
    """
    if request is None:
        return None
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        # "client, proxy1, proxy2" — the client is first. Azure appends a port.
        return forwarded.split(",")[0].strip().rsplit(":", 1)[0].strip("[]") or None
    return request.client.host if request.client else None


def record(
    db: Session,
    action: str,
    *,
    actor=None,
    actor_email: Optional[str] = None,
    target_type: Optional[str] = None,
    target_id: Optional[int] = None,
    detail: Optional[Any] = None,
    request: Optional[Request] = None,
    commit: bool = True,
) -> None:
    """
    Write one audit row.

    Never raises: an audit failure must not turn a successful action into a
    500 for the user. It is logged instead, so a broken trail is visible in
    the application log rather than silently absent.
    """
    try:
        entry = AuditLog(
            actor_id=getattr(actor, "id", None),
            actor_email=actor_email or getattr(actor, "email", None),
            ip_address=client_ip(request),
            action=action,
            target_type=target_type,
            target_id=target_id,
            detail=json.dumps(detail, default=str) if detail is not None else None,
        )
        db.add(entry)
        if commit:
            db.commit()
    except Exception:
        logger.exception("Failed to write audit log for action %s", action)
        if commit:
            try:
                db.rollback()
            except Exception:
                logger.exception("Audit rollback also failed")


def recent_failed_logins(db: Session, email: str) -> int:
    """How many times this email has failed to log in inside the window."""
    since = datetime.utcnow() - LOCKOUT_WINDOW
    return (
        db.query(AuditLog)
        .filter(
            AuditLog.action == "login.failed",
            AuditLog.actor_email == email,
            AuditLog.created_at >= since,
        )
        .count()
    )


def is_locked_out(db: Session, email: str) -> bool:
    return recent_failed_logins(db, email) >= MAX_FAILED_LOGINS


def changed_fields(before: dict, after: dict, watch: tuple) -> dict:
    """Old/new pairs for the watched keys that actually changed."""
    return {
        k: {"from": before.get(k), "to": after.get(k)}
        for k in watch
        if before.get(k) != after.get(k)
    }
