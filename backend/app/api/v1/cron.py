"""
Sweep endpoints for an external caller, authenticated by a shared secret
rather than a user token — there is no logged-in user when a scheduler calls in.

These are a fallback, not the primary path. The sweeps normally run off the
portal's own traffic (app/utils/scheduler.py), which needs no external service
and no secret held by a third party. Keep these for the case where a sweep has
to fire at a guaranteed time even if nobody signs in that day; point any HTTP
pinger at /cron/run-due with the secret as a bearer token.
"""
from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.utils import scheduler
from app.utils.leave_reminders import send_leave_reminders

router = APIRouter()


def _verify_cron_secret(authorization: str = Header(default=None)) -> None:
    # No configured secret means no legitimate caller either — refuse rather
    # than run the sweep unauthenticated.
    if not settings.CRON_SECRET or authorization != f"Bearer {settings.CRON_SECRET}":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authorized")


@router.get("/run-due")
def run_due_jobs(
    db: Session = Depends(get_db),
    _: None = Depends(_verify_cron_secret),
):
    """
    Run every sweep whose interval has elapsed.

    Safe to call as often as you like: a job that is not due is skipped, and a
    job another worker is already running is skipped too.
    """
    return scheduler.run_due(db)


@router.get("/leave-reminders")
def run_leave_reminder_sweep(
    db: Session = Depends(get_db),
    _: None = Depends(_verify_cron_secret),
):
    """Kept as a named endpoint because it predates the job registry and an
    existing schedule may still point at it. See app/utils/leave_reminders.py."""
    return send_leave_reminders(db)
