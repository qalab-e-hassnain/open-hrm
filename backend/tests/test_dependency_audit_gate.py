"""
The dependency audit can fail, and its allowlist cannot quietly stop it.

The CI step this covers used to end in `|| echo "::warning::"`. It therefore
could not fail, and it was reporting 46 high-severity findings in the
portal's dependency tree — all with fixes released — as a warning nobody
read. A green tick meant nothing about whether the dependencies were safe.

Replacing it with a hard failure raises the obvious next problem: a job that
fails on every newly-published advisory, including ones with no released fix,
gets suppressed within a fortnight. So findings can be accepted by name, with
a reason and a review date — and these tests are mostly about the review
date, because that is the only thing standing between the allowlist and the
suppression it replaced.
"""
import datetime as dt
import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPT = (Path(__file__).resolve().parent.parent.parent
          / ".github" / "scripts" / "audit_dependencies.py")


def load_script():
    """
    Import a script that lives outside the package, by path.

    It has to be registered in sys.modules before it is executed: @dataclass
    resolves a class's module through sys.modules to find its annotations,
    and on Python 3.10 it does so without checking whether the module is
    there, so leaving it out fails at the first dataclass.
    """
    spec = importlib.util.spec_from_file_location("audit_dependencies", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def audit():
    assert SCRIPT.exists(), f"{SCRIPT} is gone — CI references it"
    return load_script()


TODAY = dt.date(2026, 10, 16)


def finding(audit, advisory, *, package="somepkg", aliases=()):
    return audit.Finding(advisory=advisory, package=package, installed="1.0.0",
                         severity="high", fixed_in="",
                         aliases=frozenset(aliases))


def accepted(audit, advisory, review_by, reason="no fix released"):
    return audit.Accepted(advisory, review_by, reason)


class TestItFailsOnSomethingNobodyHasDecidedAbout:

    def test_an_unknown_finding_fails(self, audit):
        verdict = audit.classify([finding(audit, "GHSA-AAAA-BBBB-CCCC")], [],
                                 TODAY)
        assert verdict.ok is False
        assert len(verdict.unexpected) == 1

    def test_no_findings_at_all_passes(self, audit):
        assert audit.classify([], [], TODAY).ok is True

    def test_an_accepted_finding_passes(self, audit):
        verdict = audit.classify(
            [finding(audit, "GHSA-AAAA-BBBB-CCCC")],
            [accepted(audit, "GHSA-AAAA-BBBB-CCCC", dt.date(2027, 1, 1))],
            TODAY)
        assert verdict.ok is True
        assert len(verdict.tolerated) == 1

    def test_acceptance_matches_on_an_alias(self, audit):
        """
        pip-audit reports PYSEC ids, npm reports GHSA ids, and advisories
        carry CVE aliases. Whichever one somebody wrote down should work.
        """
        verdict = audit.classify(
            [finding(audit, "PYSEC-2026-1", aliases=("CVE-2026-9999",))],
            [accepted(audit, "CVE-2026-9999", dt.date(2027, 1, 1))],
            TODAY)
        assert verdict.ok is True


class TestTheReviewDateIsLoadBearing:
    """
    Without this, "accepted until we look again" means "accepted for ever",
    and the allowlist is the suppression it replaced with extra steps.
    """

    def test_an_entry_past_its_review_date_fails(self, audit):
        verdict = audit.classify(
            [finding(audit, "GHSA-AAAA-BBBB-CCCC")],
            [accepted(audit, "GHSA-AAAA-BBBB-CCCC", dt.date(2026, 10, 15))],
            TODAY)
        assert verdict.ok is False
        assert len(verdict.expired) == 1

    def test_it_fails_on_the_day_after_not_the_day_of(self, audit):
        """The review date is the last day it is still accepted."""
        entry = accepted(audit, "GHSA-AAAA-BBBB-CCCC", TODAY)
        assert audit.classify([finding(audit, "GHSA-AAAA-BBBB-CCCC")],
                              [entry], TODAY).ok is True
        assert audit.classify([finding(audit, "GHSA-AAAA-BBBB-CCCC")],
                              [entry],
                              TODAY + dt.timedelta(days=1)).ok is False

    def test_an_expired_entry_fails_even_with_no_findings_left(self, audit):
        """
        The dependency may well have been fixed. Somebody still has to
        notice and delete the entry, or the file fills up with accepted
        risks that nobody can tell apart from live ones.
        """
        verdict = audit.classify(
            [], [accepted(audit, "GHSA-AAAA-BBBB-CCCC", dt.date(2026, 1, 1))],
            TODAY)
        assert verdict.ok is False
        assert len(verdict.expired) == 1


class TestAnEntryThatMatchesNothing:

    def test_it_is_a_warning_not_a_failure(self, audit):
        """
        Usually it means the dependency was fixed. But it can also mean the
        id was mistyped, in which case a live vulnerability is not being
        accepted by the entry meant to accept it — so it has to be said out
        loud rather than ignored.
        """
        verdict = audit.classify(
            [], [accepted(audit, "GHSA-ZZZZ-ZZZZ-ZZZZ", dt.date(2027, 1, 1))],
            TODAY)
        assert verdict.ok is True
        assert len(verdict.unused) == 1


class TestTheAllowlistFileItself:

    def test_it_parses(self, audit):
        """A malformed allowlist must not read as an empty one."""
        assert isinstance(audit.read_allowlist(), list)

    def test_every_entry_has_a_reason_and_a_future_date(self, audit):
        """
        This is the test that fails in CI when an acceptance goes stale,
        which is the whole point of writing a date down.
        """
        today = dt.date.today()
        for entry in audit.read_allowlist():
            assert entry.review_by >= today, (
                f"{entry.advisory} was due for review on {entry.review_by}. "
                "Take the fix if there is one now, or move the date and say "
                "why it is still acceptable.")
            assert len(entry.reason) > 20, (
                f"{entry.advisory}: {entry.reason!r} does not explain why "
                "this is acceptable")
            assert "transitive" not in entry.reason.lower(), (
                f"{entry.advisory}: being a transitive dependency is not a "
                "reason the vulnerability does not apply")

    def test_the_ci_workflow_runs_the_gate_without_suppressing_it(self, audit):
        """
        The failure this whole file guards against was a trailing `|| echo`,
        so check for it where it would be written.
        """
        workflow = (SCRIPT.parent.parent / "workflows" / "ci.yml").read_text()
        assert "audit_dependencies.py" in workflow, (
            "CI no longer runs the dependency audit gate")
        # Comments stripped: the step carries a comment explaining what the
        # `|| echo "::warning::"` it replaced used to do, and quoting the
        # thing you are banning should not trip the ban.
        lines = [l for l in workflow[workflow.index("Dependency audit"):]
                 .splitlines() if not l.lstrip().startswith("#")]
        audit_step = "\n".join(lines)
        for suppression in ("|| true", "|| echo", "continue-on-error"):
            assert suppression not in audit_step, (
                f"the dependency audit is suppressed with {suppression!r}, "
                "which makes a green tick meaningless")
