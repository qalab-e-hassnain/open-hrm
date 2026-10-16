"""
Probation follows service, not the date the paperwork was issued.

Most of these agreements are being issued to people who have worked here for
a year or more — the contract was never issued, or it lapsed. A probation
clause running from the agreement date would restart probation for someone
with two years' service, and the agreement withholds benefits and allows a
week's notice during it, so it is not a formality.
"""
from datetime import datetime

import pytest

from app.utils.documents import _probation_context
from app.utils.leave_policy import LeavePolicyConfig


class _Employee:
    """As much of a user as the clause reads: when they joined, and whether
    anyone has recorded a probation decision about them."""

    def __init__(self, joined, probation_status=None):
        self.date_of_joining = joined
        self.probation_status = probation_status
        self.probation_end_date = None


def ctx(joined: str, today: str, status=None):
    joined_dt = datetime.fromisoformat(joined)
    return _probation_context(_Employee(joined_dt, status),
                              joined_dt,
                              datetime.fromisoformat(today))


class TestWhoIsOnProbation:
    def test_somebody_who_joined_today_is(self):
        c = ctx("2026-10-05", "2026-10-05")
        assert c["is_on_probation"] == "yes"
        assert "shall serve a probationary period" in c["probation_clause"]

    def test_somebody_two_years_in_is_not(self):
        c = ctx("2024-04-01", "2026-10-14")
        assert c["is_on_probation"] == "no"
        assert "No Probation Period applies" in c["probation_clause"]
        # The clause says so rather than staying silent: the agreement refers
        # to a Probation Period twice and defines it nowhere.
        assert "shall have effect" in c["probation_clause"]

    def test_the_day_probation_ends_it_is_over(self):
        months = LeavePolicyConfig.PROBATION_MONTHS
        assert ctx("2026-07-14", "2026-10-14")["is_on_probation"] == "no"
        assert ctx("2026-07-15", "2026-10-14")["is_on_probation"] == "yes"
        assert months == 3

    def test_service_is_counted_in_whole_months(self):
        assert ctx("2024-04-01", "2026-10-14")["service_months"] == "30"
        assert ctx("2026-09-14", "2026-10-14")["service_months"] == "1"
        assert ctx("2026-10-02", "2026-10-14")["service_months"] == "0"


class TestTheDateItEnds:
    def test_the_end_date_is_stated(self):
        assert "05 January 2027" in ctx("2026-10-05", "2026-10-05")["probation_clause"]

    def test_a_month_end_start_does_not_overflow(self):
        # 30 November plus three months is 28 February, not the 30th.
        assert "28 February 2027" in ctx("2026-11-30", "2026-11-30")["probation_clause"]

    def test_crossing_the_year_works(self):
        assert "05 March 2027" in ctx("2026-12-05", "2026-12-05")["probation_clause"]


class TestARecordedDecision:
    def test_a_direct_appointment_serves_no_probation(self):
        # Abdul Wadood: twelve days in, confirmed on appointment. Counting
        # months would give him a probation the Company never intended.
        c = ctx("2026-10-02", "2026-10-14", status="confirmed")
        assert c["is_on_probation"] == "no"
        assert "appointed directly" in c["probation_clause"]
        assert "has completed" not in c["probation_clause"]

    def test_it_does_not_exempt_the_colleague_who_joined_days_later(self):
        # Same week, no recorded decision: still a probationer.
        assert ctx("2026-10-05", "2026-10-14")["is_on_probation"] == "yes"

    def test_no_recorded_decision_never_creates_probation_for_long_service(self):
        # current_status() answers "in_probation" for anyone unreviewed, on
        # purpose. Reading that as probation here would be the original bug.
        c = ctx("2024-04-01", "2026-10-14", status=None)
        assert c["is_on_probation"] == "no"
        assert "has completed" in c["probation_clause"]


class TestTheCompletedCase:
    def test_it_names_the_real_start_of_employment(self):
        # The point of the clause for existing staff: the date employment
        # actually began, not the date somebody printed the contract.
        assert "01 April 2024" in ctx("2024-04-01", "2026-10-14")["probation_clause"]
