"""
Scheduled sweeps, driven by the portal's own traffic.

There is no scheduler available to us. Azure's F1 tier has no Always On, so the
worker is unloaded after a spell of idleness and any in-process timer dies with
it; there are no WebJobs on that tier; and more than one worker turns a naive
timer into duplicate sends. What we do have is people signing in every working
morning, which is a steadier heartbeat than a free tier's uptime.

So requests drive the sweeps. Each request asks — cheaply, at most once every
few minutes per worker — whether anything is due, and hands the work to a
background task so nobody waits on it. The claim is a conditional UPDATE
against `scheduled_jobs`, which is what stops two workers running the same
sweep at the same moment.

The cost of this design is honest and small: if nobody opens the portal all
day, the sweeps run late. Every job registered here tolerates that, because
each one processes everything outstanding rather than assuming it is being run
on any particular day.
"""
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable, Dict, Optional

from sqlalchemy import insert, or_, select, update
from sqlalchemy.orm import Session

from app.models.scheduled_job import ScheduledJob

logger = logging.getLogger(__name__)

# How long a worker may hold a claim before other workers assume it died. A
# deploy or an idle unload in the middle of a sweep is normal on F1, so an
# abandoned claim has to expire rather than wedge the job forever.
LEASE = timedelta(minutes=15)

# A worker consults the database about due jobs at most this often, no matter
# how much traffic it serves. Keeps the hook off the hot path of every request.
POLL_INTERVAL_SECONDS = 240

_last_poll_at: float = 0.0


@dataclass(frozen=True)
class Job:
    name: str
    description: str
    interval: timedelta
    run: Callable[[Session], object]

    @property
    def interval_hours(self) -> float:
        return round(self.interval.total_seconds() / 3600, 2)


def _leave_reminders(db: Session):
    from app.utils.leave_reminders import send_leave_reminders
    return send_leave_reminders(db)


def _complete_due_exits(db: Session):
    from app.utils.offboarding import complete_due_exits
    return complete_due_exits(db)


def _biometric_sync(db: Session):
    from app.utils.biometric import promote_recent, sync_logs
    result = sync_logs(db)
    if result.get("ok"):
        # Storing the scan is only half of it. Without this, somebody who
        # badges the door and never opens the portal has no attendance at all.
        result["promoted"] = promote_recent(db)
    return result


def _probation_reminders(db: Session):
    from app.utils.probation import send_probation_reminders
    return send_probation_reminders(db)


def _profile_reminders(db: Session):
    from app.utils.profile_completion import send_profile_reminders
    return send_profile_reminders(db)


def _inventory_digest(db: Session):
    from app.utils.inv_alerts import send_digests
    return send_digests(db)


def _leave_accrual(db: Session):
    from app.utils.leave_accrual import refresh_balances
    result = refresh_balances(db)
    # The detail is only interesting on the days it actually moves — the first
    # of a month, and every 1 January when the year resets.
    return {k: v for k, v in result.items() if k != "changes"} | {
        "moved": len(result.get("changes", []))}


# Daily jobs are set to 20 hours rather than 24 deliberately. On a strict day
# the sweep drifts later and later — each run schedules the next from its own
# finish time — until a day gets skipped entirely. Twenty hours guarantees one
# run per working day; every job below is safe to run more often than needed.
REGISTRY: Dict[str, Job] = {
    j.name: j for j in [
        Job("leave_reminders",
            "Email admins and HR about leave requests still undecided past their "
            "start date, or starting tomorrow.",
            timedelta(hours=20), _leave_reminders),
        Job("complete_due_exits",
            "Finalise cleared resignations whose last working day has arrived: "
            "issue the NOC, close the account, send the exit pack.",
            timedelta(hours=6), _complete_due_exits),
        Job("biometric_sync",
            "Pull the scan log from the office fingerprint device and store a "
            "first and last punch per person per day.",
            timedelta(hours=4), _biometric_sync),
        Job("probation_reminders",
            "Remind line managers and HR about probation periods ending within "
            "the next two weeks.",
            timedelta(hours=20), _probation_reminders),
        Job("leave_accrual",
            "Bring every annual-leave entitlement back in line with the months "
            "actually served this year, and reset it each January.",
            timedelta(hours=20), _leave_accrual),
        Job("inventory_digest",
            "One lab-inventory digest per person: overdue and expiring holds, "
            "stock below minimum, requests awaiting approval, quarantine and "
            "clearance still outstanding.",
            timedelta(hours=20), _inventory_digest),
        Job("profile_reminders",
            "Ask employees whose record is missing something required — bank "
            "details, CNIC, emergency contact — to fill it in.",
            timedelta(hours=20), _profile_reminders),
    ]
}


def _ensure_row(db: Session, name: str) -> None:
    """Create the bookkeeping row if this job has never run on this database."""
    exists = db.execute(
        select(ScheduledJob.name).where(ScheduledJob.name == name)).first()
    if exists:
        return
    try:
        db.execute(insert(ScheduledJob).values(name=name, run_count=0, error_count=0))
        db.commit()
    except Exception:
        # Another worker inserted it between the check and the insert. Fine.
        db.rollback()


def claim(db: Session, job: Job, force: bool = False) -> bool:
    """
    Try to take ownership of a sweep. True means this worker runs it.

    The UPDATE is the lock. Both workers issue the same statement; the row can
    only satisfy the WHERE clause once, so exactly one of them sees a row
    affected and the other walks away. `force` skips the interval check but
    still respects a live claim — a hurried admin must not start a second copy
    of a sweep that is already running.
    """
    now = datetime.utcnow()
    _ensure_row(db, job.name)
    stale = now - LEASE
    cutoff = now if force else now - job.interval
    result = db.execute(
        update(ScheduledJob)
        .where(ScheduledJob.name == job.name,
               or_(ScheduledJob.running_since.is_(None),
                   ScheduledJob.running_since < stale),
               or_(ScheduledJob.last_run_at.is_(None),
                   ScheduledJob.last_run_at < cutoff))
        .values(running_since=now)
        .execution_options(synchronize_session=False))
    db.commit()
    return result.rowcount == 1


def _release(db: Session, name: str, status: str, result, error: Optional[str],
             duration_ms: int) -> None:
    db.execute(
        update(ScheduledJob)
        .where(ScheduledJob.name == name)
        .values(
            running_since=None,
            last_run_at=datetime.utcnow(),
            last_status=status,
            # The column is JSON; a sweep that returns a bare count still has
            # to arrive as valid JSON, so anything non-dict is wrapped. Written
            # through the mapped column rather than raw SQL, because a bare
            # dict is not something a database driver can bind on its own.
            last_result=result if isinstance(result, (dict, list)) else {"value": result},
            last_error=(error or "")[:500] or None,
            last_duration_ms=duration_ms,
            run_count=ScheduledJob.run_count + 1,
            error_count=ScheduledJob.error_count + (1 if status == "error" else 0))
        .execution_options(synchronize_session=False))
    db.commit()


def run_job(db: Session, job: Job) -> dict:
    """Run one sweep that has already been claimed, and record the outcome."""
    started = time.monotonic()
    try:
        result = job.run(db)
        status = "ok"
        error = None
    except Exception as exc:            # noqa: BLE001 — the outcome is the record
        db.rollback()
        logger.exception("Scheduled job %s failed", job.name)
        result, status, error = None, "error", f"{type(exc).__name__}: {exc}"
    duration_ms = int((time.monotonic() - started) * 1000)
    try:
        _release(db, job.name, status, result, error, duration_ms)
    except Exception:
        db.rollback()
        logger.exception("Could not record the outcome of scheduled job %s", job.name)
    return {"job": job.name, "status": status, "result": result,
            "error": error, "duration_ms": duration_ms}


def run_due(db: Session, only: Optional[str] = None, force: bool = False) -> dict:
    """
    Run every job whose interval has elapsed. Jobs nobody claims are skipped.

    Returns a report rather than raising: this is called from a background task
    where an exception has nowhere useful to go.
    """
    jobs = [REGISTRY[only]] if only else list(REGISTRY.values())
    ran, skipped = [], []
    for job in jobs:
        try:
            if not claim(db, job, force=force):
                skipped.append(job.name)
                continue
        except Exception:
            db.rollback()
            logger.exception("Could not claim scheduled job %s", job.name)
            skipped.append(job.name)
            continue
        ran.append(run_job(db, job))
    return {"ran": ran, "skipped": skipped, "at": datetime.utcnow().isoformat()}


def should_poll() -> bool:
    """
    Process-level throttle so the traffic hook costs nothing on most requests.

    Check-and-set in one call, so the caller that gets True is the only one for
    the next few minutes and the rest never touch the database. Deliberately
    not locked: two threads racing here costs one extra round trip, and the
    claim settles who actually runs the sweep — a lock would cost more than the
    thing it prevents.
    """
    global _last_poll_at
    now = time.monotonic()
    if now - _last_poll_at < POLL_INTERVAL_SECONDS:
        return False
    _last_poll_at = now
    return True


def poll() -> Optional[dict]:
    """
    The traffic hook, called from a background task once should_poll() agrees.

    Opens its own session — the request's session is long closed by the time
    this runs.
    """
    from app.database import SessionLocal
    db = SessionLocal()
    try:
        return run_due(db)
    except Exception:
        logger.exception("Scheduled sweep poll failed")
        return None
    finally:
        db.close()
