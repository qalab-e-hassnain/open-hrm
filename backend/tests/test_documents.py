"""
Document rendering and integrity — the parts a signature depends on.
"""
import pytest

from app.utils.documents import (amount_in_words, ordinal, render, content_hash,
                                 missing_placeholders)


class TestAmountInWords:
    def test_matches_the_contract_wording(self):
        # The sample contract reads "(Pakistan Rupees Eighty Thousand only)".
        assert amount_in_words(80_000) == "Pakistan Rupees Eighty Thousand only"

    def test_uses_the_lakh_crore_system(self):
        # A contract governed by Pakistani law reads in the local convention,
        # not "One Hundred Forty Five Thousand".
        assert amount_in_words(145_000) == "Pakistan Rupees One Lakh Forty Five Thousand only"
        assert amount_in_words(1_250_000) == "Pakistan Rupees Twelve Lakh Fifty Thousand only"
        assert amount_in_words(30_000_000) == "Pakistan Rupees Three Crore only"

    def test_zero_and_none_are_safe(self):
        assert amount_in_words(0) == "Pakistan Rupees Zero only"
        assert amount_in_words(None) == "Pakistan Rupees Zero only"


class TestOrdinal:
    def test_common_cases(self):
        assert [ordinal(n) for n in (1, 2, 3, 4)] == ["1st", "2nd", "3rd", "4th"]

    def test_the_teens_are_all_th(self):
        # 11th not 11st — the classic ordinal bug.
        assert [ordinal(n) for n in (11, 12, 13)] == ["11th", "12th", "13th"]
        assert [ordinal(n) for n in (21, 22, 23)] == ["21st", "22nd", "23rd"]


class TestRendering:
    def test_substitutes_known_placeholders(self):
        assert render("Salary {{gross}}", {"gross": "80,000"}) == "Salary 80,000"

    def test_leaves_unknown_placeholders_visible(self):
        # A contract showing a literal {{gross_salary}} is obviously broken and
        # gets caught; one silently missing the salary line reads as complete.
        assert render("A {{missing}}", {}) == "A {{missing}}"

    def test_placeholders_are_case_sensitive(self):
        # {{Pronoun_subject}} and {{pronoun_subject}} are different keys, which
        # is how a sentence-opening pronoun gets a capital.
        ctx = {"pronoun_subject": "he", "Pronoun_subject": "He"}
        assert render("{{Pronoun_subject}} and {{pronoun_subject}}", ctx) == "He and he"

    def test_tolerates_whitespace_inside_braces(self):
        assert render("{{ name }}", {"name": "Ali"}) == "Ali"

    def test_missing_placeholders_are_reported(self):
        body = "{{a}} {{b}} {{c}}"
        assert missing_placeholders(body, {"a": "x", "b": ""}) == ["b", "c"]


class TestContentHash:
    def test_same_text_same_hash(self):
        assert content_hash("<p>hello</p>") == content_hash("<p>hello</p>")

    def test_any_change_changes_the_hash(self):
        # The whole basis of "this is what was signed".
        before = content_hash("<p>Salary 150,000</p>")
        after = content_hash("<p>Salary 999,999</p>")
        assert before != after

    def test_whitespace_counts(self):
        assert content_hash("<p>a</p>") != content_hash("<p>a </p>")


class TestPartTimeRates:
    """A part-time employee's month is shorter, and the rate must follow."""

    def test_full_time_rate_is_unchanged(self):
        from app.utils.overtime import hourly_rate_from_salary
        # 150,000 over a 160-hour month, the long-standing company figure.
        assert hourly_rate_from_salary(150_000, standard_hours_per_day=8) == 937.50
        assert hourly_rate_from_salary(150_000) == 937.50

    def test_part_time_month_is_shorter(self):
        from app.utils.overtime import monthly_hours_for
        assert monthly_hours_for(8) == 160
        assert monthly_hours_for(4) == 80

    def test_part_time_overtime_is_not_half_paid(self):
        from app.utils.overtime import calculate_flat_overtime_pay
        # Dividing a 4-hour employee's salary by 160 pays half rate for every
        # overtime hour — the defect this parameter exists to prevent.
        assert calculate_flat_overtime_pay(80_000, 2, standard_hours_per_day=4) == 2000.00
        assert calculate_flat_overtime_pay(80_000, 2, standard_hours_per_day=8) == 1000.00

    def test_an_unpaid_day_costs_a_day_either_way(self):
        from app.utils.overtime import calculate_unpaid_leave_deduction
        # Half the hours at double the rate cancel: a day off costs a day's
        # pay whether the day is four hours or eight.
        full = calculate_unpaid_leave_deduction(80_000, 1, 8.0, standard_hours_per_day=8)
        part = calculate_unpaid_leave_deduction(80_000, 1, 4.0, standard_hours_per_day=4)
        assert full == part == 4000.00


class TestJobProfileContent:
    """
    The seed content that ends up in signed contracts and in review goals.

    Guarding the shape rather than the wording: HR owns the words once the seed
    has been applied, but an empty list or a stray placeholder would silently
    produce a contract with no responsibilities in it.
    """

    def test_every_profile_has_both_lists(self):
        from app.utils.job_profile_content import PROFILES
        for designation, content in PROFILES.items():
            assert content["responsibilities"], f"{designation} has no responsibilities"
            assert content["kpis"], f"{designation} has no KPIs"

    def test_no_placeholder_text_survives_in_the_seed(self):
        from app.utils.job_profile_content import PLACEHOLDER, PROFILES
        for designation, content in PROFILES.items():
            everything = (list(content["responsibilities"]) + list(content["kpis"])
                          + [content.get("summary") or ""])
            assert not any(PLACEHOLDER in item for item in everything), designation

    def test_the_generated_boilerplate_summary_is_gone(self):
        # "is responsible for delivering the objectives of the X function to the
        # standard required by the Company" said nothing about any of these jobs.
        from app.utils.job_profile_content import PROFILES
        for designation, content in PROFILES.items():
            summary = content.get("summary") or ""
            assert "delivering the objectives of the" not in summary, designation

    def test_kpis_read_as_measures_not_virtues(self):
        # A KPI becomes a goal somebody is rated against, so it has to name
        # something observable. Adjectives on their own do not.
        from app.utils.job_profile_content import PROFILES
        vague = {"excellent", "good", "strong", "great", "proactive", "team player"}
        for designation, content in PROFILES.items():
            for kpi in content["kpis"]:
                words = set(kpi.lower().replace(",", " ").split())
                assert not (words & vague), f"{designation}: {kpi!r}"
                assert len(kpi.split()) >= 3, f"{designation}: {kpi!r} is too thin"

    def test_the_hardware_profile_keeps_what_hr_already_wrote(self):
        # The one profile that was not a placeholder. Its three original entries
        # are preserved verbatim and first; the seed only adds to them.
        from app.utils.job_profile_content import PROFILES
        hardware = PROFILES["Hardware Design Engineer"]
        assert hardware["responsibilities"][:3] == [
            "Circuit design, schematic capture and PCB layout",
            "Validation and compliance testing of hardware",
            "Vendor sourcing and quotation for components",
        ]
        assert hardware["kpis"][:3] == [
            "Design review pass rate",
            "Board bring-up within schedule",
            "Component cost against budget",
        ]
