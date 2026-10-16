"""
Visibility and manual control for the background sweeps.

Without this the sweeps are invisible: HR has no way to tell whether the leave
reminders went out this morning or whether the fingerprint device has been
unreachable for a week. Every job reports when it last ran, what it returned
and what it last failed with.
"""
from datetime import datetime, timedelta
from typing import Any, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import require_admin_or_hr
from app.database import get_db
from app.models.error_log import ErrorLog
from app.models.scheduled_job import ScheduledJob
from app.models.user import User
from app.utils import audit, scheduler

router = APIRouter()


class JobOut(BaseModel):
    name: str
    description: str
    interval_hours: float
    last_run_at: Optional[datetime] = None
    last_status: Optional[str] = None
    last_result: Optional[Any] = None
    last_error: Optional[str] = None
    last_duration_ms: Optional[int] = None
    running_since: Optional[datetime] = None
    run_count: int = 0
    error_count: int = 0
    # Derived, so the frontend does not have to redo the interval arithmetic.
    is_running: bool = False
    is_due: bool = False
    next_due_at: Optional[datetime] = None


def _state(db: Session) -> dict:
    """Bookkeeping rows keyed by job name. Read through the model so the JSON
    result column comes back decoded rather than as a string."""
    return {row.name: {
        "last_run_at": row.last_run_at, "last_status": row.last_status,
        "last_result": row.last_result, "last_error": row.last_error,
        "last_duration_ms": row.last_duration_ms, "running_since": row.running_since,
        "run_count": row.run_count, "error_count": row.error_count,
    } for row in db.query(ScheduledJob).all()}


@router.get("", response_model=List[JobOut])
def list_jobs(db: Session = Depends(get_db),
              current_user: User = Depends(require_admin_or_hr)):
    """Every registered sweep and how it last went."""
    state = _state(db)
    now = datetime.utcnow()
    out = []
    for job in scheduler.REGISTRY.values():
        row = state.get(job.name, {})
        item = JobOut(name=job.name, description=job.description,
                      interval_hours=job.interval_hours,
                      last_run_at=row.get("last_run_at"),
                      last_status=row.get("last_status"),
                      last_result=row.get("last_result"),
                      last_error=row.get("last_error"),
                      last_duration_ms=row.get("last_duration_ms"),
                      running_since=row.get("running_since"),
                      run_count=row.get("run_count") or 0,
                      error_count=row.get("error_count") or 0)
        running = row.get("running_since")
        item.is_running = bool(running and running > now - scheduler.LEASE)
        if item.last_run_at:
            item.next_due_at = item.last_run_at + job.interval
            item.is_due = item.next_due_at <= now
        else:
            item.is_due = True
        out.append(item)
    return sorted(out, key=lambda j: j.name)


@router.post("/{name}/run")
def run_job_now(name: str, request: Request, db: Session = Depends(get_db),
                current_user: User = Depends(require_admin_or_hr)):
    """
    Run one sweep immediately, ignoring its interval.

    Runs inline rather than in the background: someone who presses Run now is
    waiting to see what happened, and a result that arrives after the response
    would tell them nothing.
    """
    job = scheduler.REGISTRY.get(name)
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail=f"No scheduled job named '{name}'")
    if not scheduler.claim(db, job, force=True):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="That sweep is already running. Try again in a few minutes.")
    result = scheduler.run_job(db, job)
    audit.record(db, "scheduled_job.manual_run", actor=current_user, request=request,
                 target_type="scheduled_job", detail={"job": name,
                                                      "status": result["status"]})
    return result


class ErrorOut(BaseModel):
    reference: str
    occurred_at: datetime
    method: Optional[str] = None
    path: Optional[str] = None
    actor_email: Optional[str] = None
    exception_type: Optional[str] = None
    message: Optional[str] = None
    traceback: Optional[str] = None

    class Config:
        from_attributes = True


@router.get("/errors", response_model=List[ErrorOut])
def recent_errors(limit: int = 25, reference: Optional[str] = None,
                  path: Optional[str] = None,
                  db: Session = Depends(get_db),
                  current_user: User = Depends(require_admin_or_hr)):
    """
    Requests that failed with a 500, newest first.

    Every one carries the reference the caller was shown, so a report of "it
    said something went wrong" can be turned into the actual traceback rather
    than reproduced by guesswork.
    """
    q = db.query(ErrorLog)
    if reference:
        q = q.filter(ErrorLog.reference == reference.strip().upper())
    if path:
        q = q.filter(ErrorLog.path.ilike(f"%{path}%"))
    return q.order_by(ErrorLog.occurred_at.desc()).limit(min(limit, 200)).all()
