"""
Two people finalize the same payroll month at the same instant.

`test_payroll_run_locking.py` asserts that every pay-changing route *asks*
for `SELECT ... FOR UPDATE`. That is worth checking — a missing lock is a
one-line regression — but it proves nothing about what happens when two
requests actually collide, because it never opens a second connection. It
would pass against a database that ignored row locks entirely, which is
precisely what SQLite does.

So this file opens two real connections to a real PostgreSQL server and has
them finalize the same run at once. What it is defending is not an
abstraction: finalizing is what issues pay. Without a working lock both
transactions read the run as a draft, both pass the draft check, and both
generate a payslip for every employee on the sheet. The partial unique index
on (employee_id, month, year) then kills one of them with an integrity error
somewhere in the middle of its work, and the person who pressed the button
gets a 500 and no idea whether anybody has been paid.

The three things proved here, in order of how much they matter:

  1. exactly one of the two finalizations succeeds;
  2. the one that loses is told "409, already finalized" — not a 500;
  3. exactly one payslip exists afterwards, so nobody is paid twice.

And then, separately, that the lock is what is doing it: a `lock=True` read
waits for the holder while a `lock=False` read sails straight past. Without
that pair the tests above could be passing because the threads happened to
run one after the other.

One thing these tests established that is worth writing down, because it
changes where the credit belongs. Deleting `lock=True` from `finalize_run`
and running this file again fails (1) and (2) — the loser dies on
`uq_payroll_active_period` with an IntegrityError, which reaches the caller
as a 500 — but (3) still passes. So the partial unique index on
(employee_id, month, year) is what actually stops the employee being paid
twice; it is the backstop, and it holds even with no lock at all. The lock's
job is narrower and still worth having: it turns an integrity error raised
halfway through issuing pay, leaving the admin unable to tell what happened,
into a refusal that says which month is already finalized. Neither mechanism
makes the other redundant, and (3) is here to catch the index being dropped.
"""
import os
import threading
import time

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from app.api.v1 import payroll_runs
from app.core.security import get_password_hash
from app.database import Base
from app.models.payroll import Payroll
from app.models.payroll_run import (PayrollAuditLog, PayrollEntry, PayrollRun,
                                    PayrollRunStatusEnum)
from app.models.user import RoleEnum, User

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "").strip()
USING_POSTGRES = TEST_DATABASE_URL.startswith("postgres")

# Skipped rather than faked on SQLite. A row lock is a database behaviour, and
# a test that "passes" against a database which ignores FOR UPDATE is worse
# than no test: it reports that concurrent finalization is safe when nothing
# has checked. CI runs this job against PostgreSQL 16 — see
# .github/workflows/ci.yml, backend-postgres.
pytestmark = pytest.mark.skipif(
    not USING_POSTGRES,
    reason="needs PostgreSQL: SQLite ignores SELECT ... FOR UPDATE, so these "
           "would pass without testing anything. Set TEST_DATABASE_URL.")

MONTH = "2031-07"          # far enough out not to collide with other fixtures
SALARY = 220_000.0
HOLD_SECONDS = 1.0         # how long the lock holder keeps the row
WAIT_THRESHOLD = 0.5       # a blocked reader must have waited at least this


@pytest.fixture
def engine():
    eng = create_engine(TEST_DATABASE_URL, poolclass=NullPool)
    Base.metadata.create_all(eng, checkfirst=True)
    yield eng
    eng.dispose()


@pytest.fixture
def new_session(engine):
    """A factory for independent sessions, each on its own connection."""
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    made = []

    def open_one():
        session = factory()
        made.append(session)
        return session

    yield open_one
    for session in made:
        try:
            session.rollback()
            session.close()
        except Exception:
            pass


@pytest.fixture
def run_in_draft(engine, new_session):
    """
    A draft payroll run with one entry, committed so other connections see it.

    Everything is cleared first. These tests commit, so they cannot use the
    rollback-per-test isolation the rest of the suite relies on, and a left
    over row from a previous attempt would otherwise change what they mean.
    """
    with engine.begin() as connection:
        tables = [row[0] for row in connection.execute(text(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public' "
            "AND tablename <> 'alembic_version'"))]
        if tables:
            connection.execute(text(
                f"TRUNCATE {', '.join(tables)} RESTART IDENTITY CASCADE"))

    db = new_session()
    admin = User(email="payroll.admin@example.com", full_name="Payroll Admin",
                 hashed_password=get_password_hash("x" * 12),
                 role=RoleEnum.ADMIN, is_active=True)
    employee = User(email="paid.once@example.com", full_name="Paid Once",
                    hashed_password=get_password_hash("x" * 12),
                    role=RoleEnum.EMPLOYEE, is_active=True, base_salary=SALARY)
    db.add_all([admin, employee])
    db.flush()

    run = PayrollRun(month=MONTH, status=PayrollRunStatusEnum.DRAFT,
                     created_by=admin.id)
    db.add(run)
    db.flush()
    db.add(PayrollEntry(payroll_run_id=run.id, employee_id=employee.id,
                        monthly_salary=SALARY))
    db.commit()

    details = {"run_id": run.id, "admin_id": admin.id,
               "employee_id": employee.id}
    db.close()
    return details


def _finalize(new_session, run_id, admin_id, barrier, outcomes, slot):
    """Finalize the run from a connection of our own, and record what happened."""
    db = new_session()
    try:
        actor = db.query(User).filter(User.id == admin_id).one()
        # Both threads are inside the function before either tries for the
        # lock, so the collision is real rather than hoped for.
        barrier.wait(timeout=20)
        payroll_runs.finalize_run(run_id=run_id, db=db, current_user=actor)
        outcomes[slot] = ("finalized", None)
    except HTTPException as refused:
        outcomes[slot] = ("refused", refused.status_code)
    except Exception as broke:                    # noqa: BLE001 — recorded, then asserted on
        outcomes[slot] = ("crashed", f"{type(broke).__name__}: {broke}")
    finally:
        try:
            db.rollback()
        finally:
            db.close()


class TestTwoFinalizationsAtOnce:

    def test_exactly_one_wins_and_the_other_is_told_why(
            self, new_session, run_in_draft):
        outcomes = {}
        barrier = threading.Barrier(2)
        threads = [
            threading.Thread(target=_finalize, args=(
                new_session, run_in_draft["run_id"], run_in_draft["admin_id"],
                barrier, outcomes, slot))
            for slot in (0, 1)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=60)
            assert not thread.is_alive(), (
                "a finalization never returned — the lock is deadlocked, not "
                "serialising")

        results = sorted(outcomes.values())
        crashed = [r for r in outcomes.values() if r[0] == "crashed"]
        assert not crashed, (
            f"finalizing concurrently raised instead of refusing: {crashed}. "
            "Whoever pressed the button gets a 500 and cannot tell whether "
            "anyone was paid.")

        finalized = [r for r in outcomes.values() if r[0] == "finalized"]
        refused = [r for r in outcomes.values() if r[0] == "refused"]
        assert len(finalized) == 1, f"expected one winner, got {results}"
        assert len(refused) == 1, f"expected one refusal, got {results}"
        assert refused[0][1] == 409, (
            f"the loser got {refused[0][1]}, not 409 Conflict")

    def test_nobody_is_paid_twice(self, new_session, run_in_draft):
        """
        The assertion the rest of this is for. Two payslips for one month is
        money out of the door twice.
        """
        outcomes = {}
        barrier = threading.Barrier(2)
        threads = [
            threading.Thread(target=_finalize, args=(
                new_session, run_in_draft["run_id"], run_in_draft["admin_id"],
                barrier, outcomes, slot))
            for slot in (0, 1)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=60)

        db = new_session()
        year, month = (int(part) for part in MONTH.split("-"))
        payslips = (db.query(Payroll)
                    .filter(Payroll.employee_id == run_in_draft["employee_id"],
                            Payroll.month == month, Payroll.year == year)
                    .all())
        assert len(payslips) == 1, (
            f"{len(payslips)} payslips exist for {MONTH} after two concurrent "
            "finalizations — this employee is paid once per payslip")

        run = db.query(PayrollRun).filter(
            PayrollRun.id == run_in_draft["run_id"]).one()
        assert run.status == PayrollRunStatusEnum.FINALIZED
        assert run.finalized_at is not None

        issued = (db.query(PayrollAuditLog)
                  .filter(PayrollAuditLog.payroll_run_id == run.id,
                          PayrollAuditLog.field_changed == "run_finalized")
                  .count())
        assert issued == 1, (
            f"the audit log records the run being finalized {issued} times")

    def test_a_loser_that_rolls_back_leaves_the_run_finalizable(
            self, new_session, run_in_draft):
        """
        A lock released by a rollback rather than a commit must not leave the
        run stuck. Otherwise a crashed request wedges the month until somebody
        restarts the API.
        """
        holder = new_session()
        run = payroll_runs._get_run(holder, run_in_draft["run_id"], lock=True)
        assert run.status == PayrollRunStatusEnum.DRAFT
        holder.rollback()

        other = new_session()
        actor = other.query(User).filter(
            User.id == run_in_draft["admin_id"]).one()
        payroll_runs.finalize_run(run_id=run_in_draft["run_id"], db=other,
                                  current_user=actor)
        other.close()

        db = new_session()
        assert db.query(PayrollRun).filter(
            PayrollRun.id == run_in_draft["run_id"]
        ).one().status == PayrollRunStatusEnum.FINALIZED


class TestTheLockIsWhatDoesIt:
    """
    Without this pair, everything above could be passing because the two
    threads happened to run one after the other rather than because the
    database made them.
    """

    @staticmethod
    def _hold_then_release(new_session, run_id, acquired, finish):
        db = new_session()
        try:
            payroll_runs._get_run(db, run_id, lock=True)
            acquired.set()
            finish.wait(timeout=20)
            db.commit()
        finally:
            db.close()

    def _time_a_read(self, new_session, run_id, *, lock):
        acquired, finish = threading.Event(), threading.Event()
        holder = threading.Thread(target=self._hold_then_release,
                                  args=(new_session, run_id, acquired, finish))
        holder.start()
        assert acquired.wait(timeout=20), "the holder never took the lock"

        reader = new_session()
        released_at = [None]

        def release_after_a_moment():
            time.sleep(HOLD_SECONDS)
            released_at[0] = time.monotonic()
            finish.set()

        timer = threading.Thread(target=release_after_a_moment)
        timer.start()

        started = time.monotonic()
        payroll_runs._get_run(reader, run_id, lock=lock)
        elapsed = time.monotonic() - started

        reader.rollback()
        reader.close()
        timer.join(timeout=20)
        holder.join(timeout=20)
        return elapsed

    def test_a_locking_read_waits_for_the_holder(
            self, new_session, run_in_draft):
        elapsed = self._time_a_read(new_session, run_in_draft["run_id"],
                                    lock=True)
        assert elapsed >= WAIT_THRESHOLD, (
            f"the locking read returned after {elapsed:.3f}s while another "
            f"transaction held the row for {HOLD_SECONDS}s — it did not wait, "
            "so FOR UPDATE is not being honoured")

    def test_a_plain_read_does_not(self, new_session, run_in_draft):
        """
        The control. If this also waited, the test above would be measuring
        something other than the lock.
        """
        elapsed = self._time_a_read(new_session, run_in_draft["run_id"],
                                    lock=False)
        assert elapsed < WAIT_THRESHOLD, (
            f"a read without FOR UPDATE waited {elapsed:.3f}s, so the delay "
            "measured in the previous test is not attributable to the lock")
