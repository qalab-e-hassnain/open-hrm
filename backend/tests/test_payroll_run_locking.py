"""
Two people cannot finalize the same payroll month at once.

Finalizing is what issues pay. Without a lock, two requests both read the run
as a draft, both pass the draft check, and both generate a payslip for every
employee: one transaction wins, the other dies on the unique index, and
whoever pressed the button has no idea whether anyone got paid.

The lock is `SELECT ... FOR UPDATE` on the run row, so the second request
waits for the first to commit and is then told plainly that the run is
already finalized.

SQLite ignores FOR UPDATE entirely, so these assert that the lock is
*requested* — that every path which changes what a run will pay asks for it.
Whether the database then honours it is PostgreSQL's job, and CI runs the
suite there too.
"""
import inspect
import re

from app.api.v1 import payroll_runs


SOURCE = inspect.getsource(payroll_runs)


def body_of(function_name: str) -> str:
    """The source of one endpoint, up to the next top-level def."""
    start = SOURCE.index(f"def {function_name}(")
    rest = SOURCE[start:]
    nxt = re.search(r"\n(?:@router|def )", rest[1:])
    return rest[: nxt.start() + 1] if nxt else rest


class TestEveryPathThatChangesPayLocksTheRun:
    """
    Each of these decides, or changes, what a run will pay out. Any of them
    racing a finalize is a payslip built from figures that moved underneath
    it.
    """

    CHANGES_PAY = [
        "finalize_run",      # issues the payslips
        "reopen_run",        # takes them back
        "update_entry",      # changes one person's figures
        "add_entry",         # adds a person to the run
        "delete_entry",      # removes one
        "delete_run",        # removes the lot
    ]

    def test_each_one_takes_the_lock(self):
        missing = [name for name in self.CHANGES_PAY
                   if "lock=True" not in body_of(name)]
        assert not missing, (
            f"these change what a payroll run pays and do not lock it: {missing}")


class TestReadsDoNotLock:
    """A lock on a read blocks a finalize for no reason."""

    def test_fetching_a_run_for_display_takes_no_lock(self):
        assert "lock=True" not in body_of("get_run")


class TestTheLockIsRealSql:

    def test_get_run_asks_the_database_for_it(self):
        source = body_of("_get_run")
        assert "with_for_update()" in source

    def test_it_is_optional_and_off_by_default(self):
        signature = inspect.signature(payroll_runs._get_run)
        assert signature.parameters["lock"].default is False
