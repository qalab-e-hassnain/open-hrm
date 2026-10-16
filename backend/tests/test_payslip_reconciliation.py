"""
A payslip must add up, and the figures a person set must survive a recompute.

Uses a stand-in object rather than the ORM: payslip_reconciliation and the
component sync read plain attributes, so a database is not needed to prove the
arithmetic — and a test that needs no database gets run.
"""
import pytest

from app.api.v1.payroll import payslip_reconciliation, _sync_payroll_components


class Slip:
    """Every field the reconciliation touches, defaulted to zero."""
    def __init__(self, **kw):
        defaults = dict(
            base_salary=0.0, allowances=0.0, bonuses=0.0, arrears=0.0,
            house_allowance=0.0, transport_allowance=0.0, medical_allowance=0.0,
            other_allowance=0.0, provident_fund=0.0, social_security=0.0, eobi=0.0,
            loan_repayment=0.0, advance_recovery=0.0, other_deduction=0.0,
            deductions=0.0, reimbursement_total=0.0, overtime_pay=0.0,
            unpaid_leave_deduction=0.0, unapproved_leave_fine=0.0,
            tax=0.0, net_salary=0.0, is_historical=False, tax_is_manual=False,
        )
        defaults.update(kw)
        for k, v in defaults.items():
            setattr(self, k, v)


def _balanced(slip):
    """Set net to what the arithmetic says it should be, then check it agrees.

    `net_salary` in the returned dict is the value currently on the slip;
    `expected_net` is the computed one. Reading back the wrong key is exactly
    the mistake this helper exists to avoid making in production code.
    """
    slip.net_salary = payslip_reconciliation(slip)["expected_net"]
    return payslip_reconciliation(slip)["balanced"]


class TestReconciliation:
    def test_a_plain_salary_reconciles(self):
        assert _balanced(Slip(base_salary=145_454.55, allowances=14_545.45, tax=8_200.0))

    def test_every_line_is_accounted_for(self):
        slip = Slip(base_salary=145_454.55, allowances=14_545.45, medical_allowance=14_545.45,
                    bonuses=5_000, arrears=2_000, overtime_pay=5_000,
                    reimbursement_total=2_500, deductions=1_000,
                    unpaid_leave_deduction=14_545.44, unapproved_leave_fine=5_000, tax=8_200)
        assert _balanced(slip)

    def test_a_wrong_net_is_caught(self):
        slip = Slip(base_salary=100_000, tax=1_000, net_salary=99_999)
        assert not payslip_reconciliation(slip)["balanced"]

    def test_basic_medical_split_preserves_gross(self):
        # The company's slip structure: Basic = Gross/1.1, Medical = 10% of basic.
        gross = 160_000
        basic = round(gross / 1.1, 2)
        medical = round(gross - basic, 2)
        assert basic + medical == pytest.approx(gross)
        assert medical == pytest.approx(basic * 0.1, abs=0.02)
        assert _balanced(Slip(base_salary=basic, allowances=medical,
                              medical_allowance=medical, tax=8_200))


class TestComponentSync:
    def test_itemised_lines_are_topped_up_to_the_total(self):
        slip = Slip(allowances=20_000, medical_allowance=14_545.45)
        _sync_payroll_components(slip)
        assert slip.other_allowance == pytest.approx(20_000 - 14_545.45)

    def test_an_itemisation_exceeding_the_total_raises_the_total(self):
        # The itemisation is the more specific statement, so it wins.
        slip = Slip(allowances=1_000, medical_allowance=5_000)
        _sync_payroll_components(slip)
        assert slip.allowances == pytest.approx(5_000)


class TestCorrectedHistoricalPayslip:
    """
    A backfilled payslip carrying an unpaid-leave deduction has to add up
    exactly. It is the one shape where the deduction is stated rather than
    derived, and a payslip that is a paisa out is a payslip somebody queries.
    """

    def _july(self):
        from app.models.payroll import Payroll
        # Full month at 300,000 gross, half of it unpaid.
        return Payroll(
            employee_id=1, month=7, year=2026, is_historical=True,
            base_salary=272727.27, allowances=27272.73, medical_allowance=27272.73,
            unpaid_leave_deduction=136363.63,
            other_deduction=13636.37, deductions=13636.37,
            tax=4350.0, tax_is_manual=True, net_salary=145650.0)

    def test_the_lines_add_up_to_the_penny(self):
        from app.api.v1.payroll import payslip_reconciliation
        rec = payslip_reconciliation(self._july())
        assert rec["gross_earnings"] == 300000.00
        assert rec["total_deductions"] == 154350.00
        assert rec["expected_net"] == 145650.00
        assert rec["balanced"] is True

    def test_splitting_the_absence_avoids_a_rounding_gap(self):
        # Deducting the whole 150,000 from basic alone leaves gross at
        # 286,363.63 and the arithmetic a paisa short. Splitting it across
        # basic and the medical allowance lands on round numbers on both sides.
        p = self._july()
        assert p.base_salary + p.allowances == 300000.00
        assert round(p.unpaid_leave_deduction + p.other_deduction, 2) == 150000.00

    def test_taxable_income_is_basic_less_the_basic_portion(self):
        # The medical allowance is exempt, so clawing it back for unpaid days
        # must not move taxable income — only the basic portion does.
        p = self._july()
        assert round(p.base_salary - p.unpaid_leave_deduction, 2) == 136363.64

    def test_a_historical_row_keeps_a_stated_unpaid_deduction(self):
        from unittest.mock import MagicMock
        from app.api.v1.payroll import _recompute_payroll
        p = self._july()
        p.id = 1
        _recompute_payroll(MagicMock(), p)
        # Previously zeroed, which buried the absence inside a reduced salary.
        assert p.unpaid_leave_deduction == 136363.63
        assert p.net_salary == 145650.00
