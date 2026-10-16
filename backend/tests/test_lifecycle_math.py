"""
The arithmetic behind advances, exits, shifts and the sweep lock.

Every number here ends up in somebody's pay or somebody's record, so these are
the parts worth pinning down away from a database and an HTTP layer.
"""
from datetime import date, datetime, time, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.models.loan import EmployeeLoan, InstalmentStatusEnum, LoanKindEnum
from app.models.shift import Shift
from app.utils.loans import add_months, build_schedule, month_key
from app.utils.settlement import (_day_rate, notice_shortfall_days,
                                  recalculate_totals, years_of_service)


class TestLoanSchedule:
    def _loan(self, principal, count, first="2026-10"):
        return EmployeeLoan(id=1, employee_id=1, kind=LoanKindEnum.LOAN,
                            principal=principal, reason="x",
                            instalment_count=count, first_recovery_month=first)

    def test_splits_evenly_when_it_divides(self):
        rows = build_schedule(self._loan(40_000, 4))
        assert [r.amount for r in rows] == [10_000, 10_000, 10_000, 10_000]
        assert [r.due_month for r in rows] == ["2026-10", "2026-11", "2026-12", "2027-01"]

    def test_remainder_lands_on_the_first_instalment(self):
        # 10,000 over 3 is 3333.33 each, which is 9,999.99. The stray paisa goes
        # on the first deduction, not the last: a loan left one paisa short
        # would look unsettled for an entire extra month.
        rows = build_schedule(self._loan(10_000, 3))
        assert rows[0].amount == 3333.34
        assert rows[1].amount == rows[2].amount == 3333.33
        assert round(sum(r.amount for r in rows), 2) == 10_000.00

    def test_instalments_always_total_the_principal(self):
        for principal, count in [(7, 3), (100, 7), (99_999, 11), (1, 12)]:
            rows = build_schedule(self._loan(principal, count))
            assert round(sum(r.amount for r in rows), 2) == round(float(principal), 2)

    def test_a_single_instalment_is_the_whole_amount(self):
        rows = build_schedule(self._loan(25_000, 1))
        assert len(rows) == 1 and rows[0].amount == 25_000

    def test_every_instalment_starts_pending(self):
        assert all(r.status == InstalmentStatusEnum.PENDING
                   for r in build_schedule(self._loan(50_000, 5)))


class TestMonthKeys:
    def test_rolls_over_the_year(self):
        assert add_months("2026-11", 3) == "2027-02"
        assert add_months("2026-01", -1) == "2025-12"

    def test_zero_is_the_same_month(self):
        assert add_months("2026-07", 0) == "2026-07"

    def test_month_key_pads(self):
        assert month_key(2026, 3) == "2026-03"


class TestYearsOfService:
    def test_counts_completed_years(self):
        joined = datetime(2022, 4, 1)
        assert years_of_service(joined, date(2026, 4, 1)) == 4.0

    def test_a_part_year_over_six_months_counts_as_one(self):
        # The Ordinance's "any part thereof in excess of six months".
        joined = datetime(2022, 1, 1)
        assert years_of_service(joined, date(2026, 8, 15)) == 5.0   # 4y 7m -> 5
        assert years_of_service(joined, date(2026, 6, 15)) == 4.0   # 4y 5m -> 4

    def test_less_than_a_year_is_zero(self):
        assert years_of_service(datetime(2026, 3, 1), date(2026, 9, 1)) == 0.0

    def test_no_joining_date_is_zero_rather_than_a_crash(self):
        assert years_of_service(None, date(2026, 9, 1)) == 0.0

    def test_leaving_before_joining_is_zero(self):
        assert years_of_service(datetime(2026, 9, 1), date(2026, 1, 1)) == 0.0


class TestNoticeShortfall:
    def _resignation(self, submitted, notice_days=30):
        return SimpleNamespace(submitted_at=submitted, notice_days=notice_days)

    def test_full_notice_served_is_no_shortfall(self):
        r = self._resignation(datetime(2026, 9, 1))
        assert notice_shortfall_days(r, date(2026, 10, 1)) == 0.0

    def test_leaving_early_is_the_gap_in_calendar_days(self):
        r = self._resignation(datetime(2026, 9, 1))
        assert notice_shortfall_days(r, date(2026, 9, 21)) == 10.0

    def test_serving_longer_than_required_is_not_negative(self):
        r = self._resignation(datetime(2026, 9, 1))
        assert notice_shortfall_days(r, date(2026, 11, 1)) == 0.0

    def test_uses_the_notice_recorded_on_the_resignation(self):
        # Someone who resigned under 15 days' notice is not retrospectively
        # held to a 30-day policy.
        r = self._resignation(datetime(2026, 9, 1), notice_days=15)
        assert notice_shortfall_days(r, date(2026, 9, 16)) == 0.0


class TestDayRate:
    def test_gross_basis_is_salary_over_the_working_month(self):
        original = settings.LEAVE_ENCASHMENT_BASIS
        settings.LEAVE_ENCASHMENT_BASIS = "gross"
        try:
            assert _day_rate(80_000) == 4000.00
        finally:
            settings.LEAVE_ENCASHMENT_BASIS = original

    def test_basic_basis_strips_the_medical_allowance(self):
        # Basic Pay is Gross/1.1 on the payslip, so a basic-rate day is ~9% less.
        original = settings.LEAVE_ENCASHMENT_BASIS
        settings.LEAVE_ENCASHMENT_BASIS = "basic"
        try:
            assert _day_rate(80_000) == 3636.36
        finally:
            settings.LEAVE_ENCASHMENT_BASIS = original


class TestSettlementTotals:
    def _blank(self, **kw):
        base = dict(final_month_amount=0.0, leave_encashment_amount=0.0,
                    gratuity_amount=0.0, pending_reimbursements=0.0,
                    other_earning=0.0, notice_shortfall_amount=0.0,
                    loan_outstanding=0.0, asset_recovery=0.0,
                    tax_deduction=0.0, other_deduction=0.0)
        base.update(kw)
        return SimpleNamespace(**base)

    def test_net_is_earnings_less_deductions(self):
        s = self._blank(final_month_amount=40_000, leave_encashment_amount=12_000,
                        loan_outstanding=15_000, tax_deduction=2_000)
        recalculate_totals(s)
        assert s.gross_payable == 52_000
        assert s.total_deductions == 17_000
        assert s.net_payable == 35_000

    def test_a_leaver_can_owe_the_company(self):
        # Legitimate: someone leaving mid-notice with an outstanding loan.
        s = self._blank(final_month_amount=10_000, loan_outstanding=25_000)
        recalculate_totals(s)
        assert s.net_payable == -15_000

    def test_nothing_owed_either_way_totals_zero(self):
        s = self._blank()
        recalculate_totals(s)
        assert (s.gross_payable, s.total_deductions, s.net_payable) == (0.0, 0.0, 0.0)


class TestShiftHours:
    def test_paid_hours_exclude_the_break(self):
        shift = Shift(name="General", start_time=time(9, 0), end_time=time(18, 0),
                      break_minutes=60)
        assert shift.hours == 8.0

    def test_a_half_day_shift(self):
        shift = Shift(name="Morning half", start_time=time(9, 0), end_time=time(13, 0),
                      break_minutes=0)
        assert shift.hours == 4.0

    def test_a_shift_crossing_midnight(self):
        shift = Shift(name="Night", start_time=time(22, 0), end_time=time(6, 0),
                      break_minutes=30)
        assert shift.hours == 7.5

    def test_working_days_read_monday_first(self):
        weekdays = Shift(name="Weekdays", start_time=time(9), end_time=time(17),
                         days_of_week="1111100")
        assert weekdays.works_on(date(2026, 9, 7))       # a Monday
        assert not weekdays.works_on(date(2026, 9, 12))  # a Saturday

    def test_a_weekend_pattern(self):
        weekend = Shift(name="Weekend cover", start_time=time(10), end_time=time(16),
                        days_of_week="0000011")
        assert not weekend.works_on(date(2026, 9, 7))
        assert weekend.works_on(date(2026, 9, 13))       # a Sunday


class TestSweepClaim:
    """
    The claim is the only thing stopping two workers running the same sweep, so
    it is tested against a real database rather than reasoned about.
    """

    @pytest.fixture
    def db(self):
        from app.database import Base
        from app.models.scheduled_job import ScheduledJob  # noqa: F401

        engine = create_engine("sqlite://")
        ScheduledJob.__table__.create(engine)
        session = sessionmaker(bind=engine)()
        yield session
        session.close()

    def _job(self, hours=20):
        from app.utils.scheduler import Job
        return Job("test_job", "a test sweep", timedelta(hours=hours),
                   lambda db: {"ok": True})

    def test_only_one_caller_wins_the_claim(self, db):
        from app.utils import scheduler
        job = self._job()
        assert scheduler.claim(db, job) is True
        # A second worker arriving while the first still holds it.
        assert scheduler.claim(db, job) is False

    def test_a_job_run_recently_is_not_due(self, db):
        from app.utils import scheduler
        job = self._job()
        assert scheduler.claim(db, job) is True
        scheduler.run_job(db, job)
        assert scheduler.claim(db, job) is False

    def test_force_ignores_the_interval(self, db):
        from app.utils import scheduler
        job = self._job()
        scheduler.claim(db, job)
        scheduler.run_job(db, job)
        assert scheduler.claim(db, job, force=True) is True

    def test_force_still_respects_a_live_claim(self, db):
        from app.utils import scheduler
        job = self._job()
        assert scheduler.claim(db, job) is True
        # Someone pressing "Run now" must not start a second copy of a sweep
        # that is already running.
        assert scheduler.claim(db, job, force=True) is False

    def test_an_abandoned_claim_expires(self, db):
        from sqlalchemy import text
        from app.utils import scheduler
        job = self._job()
        assert scheduler.claim(db, job) is True
        # A worker killed mid-sweep — a deploy, or an idle unload on F1.
        db.execute(text("UPDATE scheduled_jobs SET running_since = :t"),
                   {"t": datetime.utcnow() - scheduler.LEASE - timedelta(minutes=1)})
        db.commit()
        assert scheduler.claim(db, job) is True

    def test_a_failing_sweep_is_recorded_not_raised(self, db):
        from app.utils import scheduler

        def explode(_db):
            raise RuntimeError("device unreachable")

        job = scheduler.Job("flaky", "", timedelta(hours=1), explode)
        scheduler.claim(db, job)
        result = scheduler.run_job(db, job)
        assert result["status"] == "error"
        assert "device unreachable" in result["error"]
        # And the claim is released, so the next run is not blocked by a
        # failure that already finished.
        assert scheduler.claim(db, job, force=True) is True

    def test_the_outcome_is_written_back(self, db):
        from sqlalchemy import text
        from app.utils import scheduler
        job = self._job()
        scheduler.claim(db, job)
        scheduler.run_job(db, job)
        row = db.execute(text(
            "SELECT last_status, run_count, running_since FROM scheduled_jobs"
        )).first()
        assert row[0] == "ok" and row[1] == 1 and row[2] is None


class TestSignedFileUrls:
    """
    A browser loading an <img> sends no Authorization header. These signatures
    are the only reason a private profile picture renders at all, so the
    forgery cases matter as much as the happy one.
    """

    def test_a_fresh_signature_verifies(self):
        from app.utils.signed_urls import sign, verify
        exp, sig = sign("abc123.png", 60)
        assert verify("abc123.png", exp, sig) is True

    def test_a_signature_is_worth_exactly_one_object(self):
        from app.utils.signed_urls import sign, verify
        exp, sig = sign("abc123.png", 60)
        assert verify("someone-elses-cnic.jpg", exp, sig) is False

    def test_tampering_with_the_expiry_invalidates_it(self):
        from app.utils.signed_urls import sign, verify
        exp, sig = sign("abc123.png", 60)
        # Trying to extend the life of a link you already hold.
        assert verify("abc123.png", exp + 86400, sig) is False

    def test_an_expired_signature_is_refused(self):
        from app.utils.signed_urls import sign, verify
        exp, sig = sign("abc123.png", -60)
        assert verify("abc123.png", exp, sig) is False

    def test_nothing_at_all_is_refused(self):
        from app.utils.signed_urls import verify
        assert verify("abc123.png", None, None) is False
        assert verify("abc123.png", "not-a-number", "x" * 32) is False

    def test_signing_appends_to_the_stored_url(self):
        from app.utils.signed_urls import object_name_from, sign_url, verify
        from urllib.parse import parse_qs, urlsplit
        url = "https://api.example.com/api/v1/upload/abc123.png"
        signed = sign_url(url)
        q = parse_qs(urlsplit(signed).query)
        assert signed.startswith(url + "?")
        assert verify(object_name_from(url), int(q["exp"][0]), q["sig"][0])

    def test_nothing_to_sign_passes_through(self):
        from app.utils.signed_urls import sign_url
        assert sign_url(None) is None
        assert sign_url("") == ""
        # Not one of ours, and left alone rather than mangled.
        already = "https://cdn.example.com/x.png?v=2"
        assert sign_url(already) == already


class TestBiometricLogParsing:
    """
    The device's event log, which replaced a CSV export that stated no timezone
    and pre-aggregated the day for us.
    """

    def _ev(self, label, stamp, log_id=1):
        return [log_id, "24:0A:C4:C0:82:D4", label, stamp]

    def test_the_method_suffix_is_not_part_of_the_name(self):
        from app.utils.biometric import normalise_label
        assert normalise_label("Kamran (Fingerprint)") == "Kamran"
        assert normalise_label("Zeeshan(Fingerprint)") == "Zeeshan"
        assert normalise_label("Kamran") == "Kamran"
        # Three identities were mapped with the suffix baked in; both spellings
        # have to fold to the same name or they quietly stop matching.
        assert normalise_label("huzaifa(Fingerprint)") == normalise_label("huzaifa")

    def test_the_door_button_is_not_a_person(self):
        from app.utils.biometric import parse_logs
        rows = parse_logs([
            self._ev("Button", "2026-07-18 13:31:25.170803+05:00"),
            self._ev("Halt ON with Button", "2026-07-18 13:32:25+05:00"),
            self._ev("Restarting 3 am", "2026-07-18 03:00:00+05:00"),
            self._ev("Kamran (Fingerprint)", "2026-07-18 13:27:46.157734+05:00"),
        ])
        assert [r["device_name"] for r in rows] == ["Kamran"]

    def test_the_stated_offset_is_used_rather_than_assumed(self):
        from datetime import datetime
        from app.utils.biometric import parse_logs
        rows = parse_logs([self._ev("Bilal", "2026-07-18 13:27:46+05:00")])
        # 13:27 in Pakistan is 08:27 UTC. Stored naive-UTC, as the column is.
        assert rows[0]["first_scan"] == datetime(2026, 7, 18, 8, 27, 46)

    def test_a_day_is_a_local_day(self):
        from app.utils.biometric import parse_logs
        rows = parse_logs([
            self._ev("Zaeem", "2026-07-18 00:30:00+05:00"),
            self._ev("Zaeem", "2026-07-18 22:45:00+05:00"),
        ])
        # Both are the 18th where the office is. Grouping on the UTC date would
        # push the first one back to the 17th — a quiet off-by-one that makes an
        # attendance report untrustworthy without ever looking wrong.
        assert len(rows) == 1
        assert str(rows[0]["scan_date"]) == "2026-07-18"
        assert rows[0]["scans"] == 2

    def test_first_and_last_survive_a_busy_day(self):
        from app.utils.biometric import parse_logs
        rows = parse_logs([
            self._ev("Qalab", "2026-07-18 14:00:00+05:00"),
            self._ev("Qalab", "2026-07-18 09:15:00+05:00"),
            self._ev("Qalab", "2026-07-18 19:40:00+05:00"),
            self._ev("Qalab", "2026-07-18 11:05:00+05:00"),
        ])
        r = rows[0]
        assert r["scans"] == 4
        assert r["first_scan"].strftime("%H:%M") == "04:15"   # 09:15 PKT
        assert r["last_scan"].strftime("%H:%M") == "14:40"    # 19:40 PKT
        assert r["scans"] > 1

    def test_one_punch_is_a_day_with_no_known_end(self):
        from app.utils.biometric import parse_logs
        rows = parse_logs([self._ev("Bilal", "2026-07-18 09:15:00+05:00")])
        assert rows[0]["scans"] == 1
        assert rows[0]["first_scan"] == rows[0]["last_scan"]

    def test_the_window_drops_older_events(self):
        from datetime import date
        from app.utils.biometric import parse_logs
        events = [self._ev("Bilal", "2025-04-01 09:00:00+05:00"),
                  self._ev("Bilal", "2026-09-01 09:00:00+05:00")]
        rows = parse_logs(events, since=date(2026, 8, 1))
        assert [str(r["scan_date"]) for r in rows] == ["2026-09-01"]

    def test_malformed_events_are_skipped_not_fatal(self):
        from app.utils.biometric import parse_logs
        rows = parse_logs([
            self._ev("Bilal", "not a timestamp"),
            ["short", "row"],
            self._ev("", "2026-07-18 09:00:00+05:00"),
            self._ev("Bilal", "2026-07-18 09:00:00+05:00"),
        ])
        assert len(rows) == 1


class TestNaiveUTCBoundary:
    """
    A browser sends `2026-09-07T00:00:00.000Z`; this system stores naive UTC.
    Mixing them raised a TypeError on comparison and produced a 500 on the
    manual time-log form. Silently storing an aware value in a naive column is
    the worse half of the same bug.
    """

    def test_a_z_suffixed_time_becomes_naive_utc(self):
        from datetime import datetime, timezone
        from app.schemas.datetimes import to_naive_utc
        aware = datetime(2026, 9, 7, 7, 0, tzinfo=timezone.utc)
        assert to_naive_utc(aware) == datetime(2026, 9, 7, 7, 0)
        assert to_naive_utc(aware).tzinfo is None

    def test_an_offset_is_converted_not_discarded(self):
        from datetime import datetime, timedelta, timezone
        from app.schemas.datetimes import to_naive_utc
        # Noon in Pakistan is 07:00 UTC. Dropping the tzinfo without converting
        # would store noon and be five hours wrong.
        pkt = datetime(2026, 9, 7, 12, 0, tzinfo=timezone(timedelta(hours=5)))
        assert to_naive_utc(pkt) == datetime(2026, 9, 7, 7, 0)

    def test_naive_input_is_left_alone(self):
        from datetime import datetime
        from app.schemas.datetimes import to_naive_utc
        naive = datetime(2026, 9, 7, 12, 0)
        assert to_naive_utc(naive) == naive

    def test_none_passes_through(self):
        from app.schemas.datetimes import to_naive_utc
        assert to_naive_utc(None) is None

    def test_the_schema_normalises_on_the_way_in(self):
        from datetime import datetime
        from app.schemas.time_log import TimeLogCreate
        payload = TimeLogCreate(
            kind="attendance", work_date="2026-09-07T00:00:00.000Z",
            check_in_time="2026-09-07T12:00:00+05:00",
            check_out_time="2026-09-07T20:00:00+05:00",
            reason="Forgot to clock in")
        # All three arrive naive, so the endpoint can compare them against
        # datetime.utcnow() without raising.
        assert payload.work_date.tzinfo is None
        assert payload.check_in_time == datetime(2026, 9, 7, 7, 0)
        assert payload.check_out_time == datetime(2026, 9, 7, 15, 0)

    def test_the_comparison_that_used_to_raise(self):
        from datetime import datetime, timedelta
        from app.schemas.time_log import TimeLogCreate
        payload = TimeLogCreate(
            kind="overtime", work_date="2026-09-07T00:00:00.000Z",
            hours=2.0, reason="Stayed late")
        # This exact line was the 500.
        assert (payload.work_date > datetime.utcnow() + timedelta(days=1)) in (True, False)
