"""
The same overtime must not reach a payslip twice.

The payroll sheet has an "Allowance / overtime" column HR fills in by hand,
and the payroll run independently sweeps approved time-log claims into
overtime_pay. Nothing connected the two, so Huzaifa's September payslip
carried Rs 1,875 of overtime under both headings.
"""
from app.api.v1.payroll import _is_sheet_overtime

SHEET_NOTE = "Allowance / overtime (payroll sheet)"


class _Payslip:
    def __init__(self, note=None, other=0.0):
        self.other_allowance_note = note
        self.other_allowance = other


class TestRecognisingTheSheetEntry:
    def test_the_sheet_note_is_recognised(self):
        assert _is_sheet_overtime(_Payslip(SHEET_NOTE)) is True

    def test_case_does_not_matter(self):
        assert _is_sheet_overtime(_Payslip(SHEET_NOTE.upper())) is True

    def test_a_hand_written_allowance_is_left_alone(self):
        # Someone's genuine one-off allowance must survive: only the sheet's
        # combined overtime column is dropped.
        assert _is_sheet_overtime(_Payslip("Travel allowance, agreed with HR")) is False
        assert _is_sheet_overtime(_Payslip("Fuel top-up")) is False

    def test_a_note_mentioning_only_one_half_is_not_enough(self):
        assert _is_sheet_overtime(_Payslip("overtime agreed verbally")) is False
        assert _is_sheet_overtime(_Payslip("payroll sheet adjustment")) is False

    def test_no_note_at_all(self):
        assert _is_sheet_overtime(_Payslip(None)) is False
        assert _is_sheet_overtime(_Payslip("")) is False


class TestTheArithmeticThatWentWrong:
    """Huzaifa's September payslip, as it stood and as it should have."""

    BASIC, MEDICAL, SHEET_OT, CLAIM_OT, TAX = 109090.91, 10909.09, 1875.0, 1875.0, 1380.0

    def test_the_bug_the_numbers_showed(self):
        allowances = self.MEDICAL + self.SHEET_OT
        gross = self.BASIC + allowances + self.CLAIM_OT
        assert round(gross - self.TAX, 2) == 122370.00     # what was stored

    def test_dropping_the_sheet_entry_gives_the_right_net(self):
        allowances = self.MEDICAL                           # sheet overtime removed
        gross = self.BASIC + allowances + self.CLAIM_OT
        assert round(gross - self.TAX, 2) == 120495.00

    def test_the_difference_is_exactly_the_overtime(self):
        assert round(122370.00 - 120495.00, 2) == self.CLAIM_OT
