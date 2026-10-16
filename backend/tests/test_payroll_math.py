"""
Payroll arithmetic.

These cover the pure functions — no database, no fixtures — because that is
where a wrong number becomes someone's wrong salary, and because they can then
run anywhere in under a second.
"""
import pytest

from app.utils.tax import (calculate_annual_income_tax, calculate_monthly_income_tax,
                           pk_tax_year_position, TAX_FREE_THRESHOLD_ANNUAL)
from app.utils.overtime import (hourly_rate_from_salary, calculate_flat_overtime_pay,
                                split_day_hours, MONTHLY_WORKING_HOURS)
from app.utils.leave_policy import (calculate_working_days, is_personal_notice_sufficient,
                                    is_advance_notice_sufficient, requires_medical_certificate,
                                    LeavePolicyConfig)
from datetime import datetime, timedelta


class TestIncomeTax:
    def test_below_threshold_is_exempt(self):
        assert calculate_annual_income_tax(TAX_FREE_THRESHOLD_ANNUAL) == 0.0
        assert calculate_annual_income_tax(599_999) == 0.0

    def test_slab_boundaries(self):
        # First rupee over the threshold is taxed at 1%, not the whole amount.
        assert calculate_annual_income_tax(700_000) == pytest.approx(1_000.0)
        # Base + rate on the excess, not a flat rate on the total.
        assert calculate_annual_income_tax(1_200_000) == pytest.approx(6_000.0)
        assert calculate_annual_income_tax(2_200_000) == pytest.approx(116_000.0)

    def test_top_slab_extends_beyond_the_table(self):
        assert calculate_annual_income_tax(10_000_000) == pytest.approx(
            1_424_000 + (10_000_000 - 7_000_000) * 0.35)

    def test_zero_and_negative_are_safe(self):
        assert calculate_annual_income_tax(0) == 0.0
        assert calculate_annual_income_tax(-5000) == 0.0
        assert calculate_monthly_income_tax(None) == 0.0

    def test_monthly_is_the_annual_figure_spread(self):
        assert calculate_monthly_income_tax(100_000) == pytest.approx(
            calculate_annual_income_tax(1_200_000) / 12)

    def test_pakistan_tax_year_starts_in_july(self):
        assert pk_tax_year_position(7) == 1     # July is month 1
        assert pk_tax_year_position(6) == 12    # June is month 12
        assert pk_tax_year_position(1) == 7     # January is month 7


class TestOvertimePay:
    def test_hourly_rate_is_salary_over_a_160_hour_month(self):
        assert MONTHLY_WORKING_HOURS == 160
        assert hourly_rate_from_salary(160_000) == pytest.approx(1_000.0)
        assert hourly_rate_from_salary(150_000) == pytest.approx(937.50)

    def test_pay_is_pro_rata_including_fractions(self):
        # The property that matters: a fraction of an hour pays its fraction.
        for hours, expected in [(1, 1_000), (1.5, 1_500), (2.25, 2_250), (8, 8_000)]:
            assert calculate_flat_overtime_pay(160_000, hours) == pytest.approx(expected)

    def test_no_hours_no_pay(self):
        assert calculate_flat_overtime_pay(160_000, 0) == 0.0
        assert calculate_flat_overtime_pay(160_000, None) == 0.0

    def test_no_salary_means_no_rate(self):
        # Guards the bug where overtime silently paid zero because the rate
        # came from an unset column.
        assert hourly_rate_from_salary(0) == 0.0
        assert hourly_rate_from_salary(None) == 0.0


class TestDaySplit:
    def test_a_short_day_is_all_regular(self):
        parts = split_day_hours([6.0], standard_hours=8.0)
        assert parts[0]["regular_hours"] == pytest.approx(6.0)
        assert parts[0]["overtime_hours"] == pytest.approx(0.0)

    def test_overtime_begins_after_the_standard_day(self):
        parts = split_day_hours([10.0], standard_hours=8.0)
        assert parts[0]["regular_hours"] == pytest.approx(8.0)
        assert parts[0]["overtime_hours"] == pytest.approx(2.0)

    def test_the_cap_applies_across_sessions_not_per_session(self):
        # Two 5-hour sessions is a 10-hour day: 8 regular, 2 overtime — not
        # two short days with no overtime at all.
        parts = split_day_hours([5.0, 5.0], standard_hours=8.0)
        assert sum(p["regular_hours"] for p in parts) == pytest.approx(8.0)
        assert sum(p["overtime_hours"] for p in parts) == pytest.approx(2.0)


class TestLeavePolicy:
    def test_working_days_exclude_weekends(self):
        # Mon 7 Sep 2026 to Fri 11 Sep 2026
        assert calculate_working_days(datetime(2026, 9, 7), datetime(2026, 9, 11)) == 5
        # spanning the weekend adds no days
        assert calculate_working_days(datetime(2026, 9, 11), datetime(2026, 9, 14)) == 2

    def test_reversed_range_is_zero_not_negative(self):
        assert calculate_working_days(datetime(2026, 9, 11), datetime(2026, 9, 7)) == 0

    def test_personal_leave_needs_48_hours(self):
        now = datetime(2026, 9, 1, 10, 0)
        assert LeavePolicyConfig.PERSONAL_LEAVE_NOTICE_HOURS == 48
        assert is_personal_notice_sufficient(now, now + timedelta(hours=48))
        assert not is_personal_notice_sufficient(now, now + timedelta(hours=47))

    def test_notice_is_measured_in_hours_not_whole_days(self):
        # Monday evening for Wednesday morning is ~48h and must pass, even
        # though a day-granularity check would score it as 1 day.
        monday_evening = datetime(2026, 9, 7, 18, 0)
        wednesday_morning = datetime(2026, 9, 9, 18, 0)
        assert is_personal_notice_sufficient(monday_evening, wednesday_morning)

    def test_annual_leave_keeps_the_two_week_rule(self):
        now = datetime(2026, 9, 1)
        assert is_advance_notice_sufficient(now, now + timedelta(days=14))
        assert not is_advance_notice_sufficient(now, now + timedelta(days=13))

    def test_sick_leave_needs_documentation_from_day_one(self):
        assert requires_medical_certificate("sick", 1)
        assert requires_medical_certificate("sick", 5)
        assert not requires_medical_certificate("annual", 5)

    def test_annual_allowance_is_the_revised_figure(self):
        assert LeavePolicyConfig.ANNUAL_LEAVE_PER_YEAR == 14
