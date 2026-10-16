"""
Bookkeeping for the background sweeps.

One row per registered job, holding when it last ran and whether a worker is
running it now. The row is the lock as well as the record: claiming a job is a
conditional UPDATE, so two workers handling two requests at the same moment
cannot both start the same sweep. See app/utils/scheduler.py.
"""
from sqlalchemy import Column, Integer, String, DateTime, JSON

from app.database import Base


class ScheduledJob(Base):
    __tablename__ = "scheduled_jobs"

    # The registry key, not a surrogate id — a job is identified by its name
    # everywhere else, and there is exactly one row per name.
    name = Column(String, primary_key=True)

    last_run_at = Column(DateTime, nullable=True)
    last_status = Column(String, nullable=True)      # ok | error
    last_result = Column(JSON, nullable=True)        # whatever the sweep returned
    last_error = Column(String, nullable=True)
    last_duration_ms = Column(Integer, nullable=True)

    # Non-null while a worker holds the claim. A stale value (older than the
    # lease) is treated as abandoned, so a worker killed mid-sweep — which on
    # F1 means any deploy or idle unload — does not wedge the job forever.
    running_since = Column(DateTime, nullable=True)

    run_count = Column(Integer, default=0, nullable=False)
    error_count = Column(Integer, default=0, nullable=False)
