"""
A reimbursement is paid once.

It leaves the company one of two ways — swept into a payslip, or handed over
separately — and the only thing stopping it going both ways is the register
knowing which already happened.
"""
from datetime import datetime

import pytest

from app.models.payroll import PayrollStatusEnum
from app.models.reimbursement import Reimbursement, ReimbursementStatusEnum
from app.utils.reimbursements import payment_route, settle_with_payroll


class _Payroll:
    def __init__(self, id=1, status=PayrollStatusEnum.DRAFT, payment_date=None):
        self.id, self.status, self.payment_date = id, status, payment_date
        self.month, self.year = 8, 2026


class _Query:
    def __init__(self, rows): self._rows = rows
    def filter(self, *a, **k): return self
    def all(self): return self._rows


class _Db:
    def __init__(self, rows): self._rows = rows
    def query(self, *a, **k): return _Query(self._rows)


def _claim(status=ReimbursementStatusEnum.APPROVED, payroll_id=None):
    r = Reimbursement(employee_id=1, amount=700.0, description="x",
                      date_of_expense=datetime(2026, 8, 25))
    r.status, r.payroll_id, r.paid_at = status, payroll_id, None
    return r


class TestSettleWithPayroll:
    def test_a_paid_payslip_settles_what_it_carried(self):
        claim = _claim(payroll_id=5)
        payroll = _Payroll(id=5, status=PayrollStatusEnum.PAID,
                           payment_date=datetime(2026, 9, 1))
        assert settle_with_payroll(_Db([claim]), payroll) == 1
        assert claim.status == ReimbursementStatusEnum.PAID
        # Paid when the salary was, not when somebody got round to the sweep.
        assert claim.paid_at == datetime(2026, 9, 1)

    def test_a_claim_already_paid_separately_is_left_alone(self):
        claim = _claim(status=ReimbursementStatusEnum.PAID, payroll_id=5)
        claim.paid_at = datetime(2026, 8, 20)
        assert settle_with_payroll(_Db([claim]), _Payroll(id=5)) == 0
        assert claim.paid_at == datetime(2026, 8, 20)

    def test_a_rejected_claim_is_never_settled(self):
        claim = _claim(status=ReimbursementStatusEnum.REJECTED, payroll_id=5)
        assert settle_with_payroll(_Db([claim]), _Payroll(id=5)) == 0
        assert claim.status == ReimbursementStatusEnum.REJECTED

    def test_a_payslip_with_no_payment_date_records_none(self):
        # Borrowing today's date would put "paid on 16 September" against an
        # August salary. Paid with no date recorded is the true version.
        claim = _claim(payroll_id=5)
        assert settle_with_payroll(_Db([claim]), _Payroll(id=5)) == 1
        assert claim.status == ReimbursementStatusEnum.PAID
        assert claim.paid_at is None


class TestPaymentRoute:
    def test_unpaid_has_no_route(self):
        assert payment_route(_claim()) == ""

    def test_paid_with_a_payslip_reads_as_payroll(self):
        assert payment_route(
            _claim(status=ReimbursementStatusEnum.PAID, payroll_id=5)) == "payroll"

    def test_paid_with_no_payslip_reads_as_separate(self):
        assert payment_route(
            _claim(status=ReimbursementStatusEnum.PAID)) == "separate"

    def test_an_approved_claim_sitting_on_a_draft_payslip_is_not_paid_yet(self):
        # payroll_id alone must not read as paid — the payslip may still be a
        # draft, and the money has not moved.
        assert payment_route(_claim(payroll_id=5)) == ""
