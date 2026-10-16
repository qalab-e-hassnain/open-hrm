"""
Nobody signs off their own claim, and an issued payslip cannot be rewritten.

Both of these were found by an auditor's eye rather than a failing test. The
time-log endpoint already refused self-approval and that was not enough: a
maintenance script wrote ten approved claims straight to the table, naming the
employee as both the line manager and the HR approver. The rows were
indistinguishable from properly approved ones.

A rule enforced in one endpoint holds until somebody writes a second way in,
so the time-log rule now lives on the model, where every writer meets it.
"""
import pytest

from app.models.payroll import Payroll, PayrollStatusEnum
from app.models.time_log import (SelfApproval, TimeLogKindEnum, TimeLogRequest,
                                 TimeLogStatusEnum)


class TestATimeLogCannotBeSelfApproved:

    def test_the_model_refuses_it_whatever_wrote_it(self, env):
        """A script bypasses the endpoint; it does not bypass this."""
        from datetime import datetime
        db = env.Session()
        try:
            db.add(TimeLogRequest(
                employee_id=env.ids["eng"], kind=TimeLogKindEnum.ATTENDANCE,
                work_date=datetime.utcnow(), hours=8.0, reason="worked as normal",
                status=TimeLogStatusEnum.APPROVED,
                approved_by=env.ids["eng"], approved_at=datetime.utcnow()))
            with pytest.raises(SelfApproval):
                db.flush()
        finally:
            db.rollback()
            db.close()

    def test_the_manager_signature_is_covered_too(self, env):
        from datetime import datetime
        db = env.Session()
        try:
            db.add(TimeLogRequest(
                employee_id=env.ids["eng"], kind=TimeLogKindEnum.ATTENDANCE,
                work_date=datetime.utcnow(), hours=8.0, reason="worked as normal",
                status=TimeLogStatusEnum.MANAGER_APPROVED,
                manager_approved_by=env.ids["eng"],
                manager_approved_at=datetime.utcnow()))
            with pytest.raises(SelfApproval):
                db.flush()
        finally:
            db.rollback()
            db.close()

    def test_somebody_else_signing_is_fine(self, env):
        from datetime import datetime
        db = env.Session()
        try:
            db.add(TimeLogRequest(
                employee_id=env.ids["eng"], kind=TimeLogKindEnum.ATTENDANCE,
                work_date=datetime.utcnow(), hours=8.0, reason="worked as normal",
                status=TimeLogStatusEnum.APPROVED,
                manager_approved_by=env.ids["lead"],
                approved_by=env.ids["admin"], approved_at=datetime.utcnow()))
            db.flush()          # no exception
        finally:
            db.rollback()
            db.close()


class TestLeaveIsNotSelfApproved:

    def test_an_admin_cannot_decide_their_own_leave(self, env):
        from datetime import datetime, timedelta
        from app.models.leave import Leave, LeaveStatusEnum, LeaveTypeEnum

        db = env.Session()
        try:
            leave = Leave(employee_id=env.ids["admin"], leave_type=LeaveTypeEnum.ANNUAL,
                          start_date=datetime.utcnow() + timedelta(days=3),
                          end_date=datetime.utcnow() + timedelta(days=4),
                          total_days=2, reason="a break",
                          status=LeaveStatusEnum.PENDING)
            db.add(leave)
            db.commit()
            leave_id = leave.id
        finally:
            db.close()

        res = env.put("admin", f"/api/v1/leaves/{leave_id}/approve",
                      {"status": "approved"})
        assert res.status_code == 403
        assert "own leave" in res.json()["detail"].lower()


class TestAnIssuedPayslipIsLocked:
    """
    Processed means the employee has been told what they will be paid.
    Rewriting that leaves no trace it ever said anything else, and deleting it
    releases the reimbursements and loan instalments it carried as though they
    were never paid.
    """

    def _payslip(self, env, status):
        db = env.Session()
        try:
            row = Payroll(employee_id=env.ids["eng"], month=9, year=2026,
                          base_salary=100000.0, net_salary=90000.0, tax=0.0,
                          status=status)
            db.add(row)
            db.commit()
            return row.id
        finally:
            db.close()

    @pytest.mark.parametrize("status", [PayrollStatusEnum.PROCESSED,
                                        PayrollStatusEnum.PAID])
    def test_it_cannot_be_edited(self, env, status):
        payroll_id = self._payslip(env, status)
        res = env.put("admin", f"/api/v1/payroll/{payroll_id}",
                      {"base_salary": 1.0})
        assert res.status_code == 409
        assert "correct" in res.json()["detail"].lower()

    @pytest.mark.parametrize("status", [PayrollStatusEnum.PROCESSED,
                                        PayrollStatusEnum.PAID])
    def test_it_cannot_be_deleted(self, env, status):
        payroll_id = self._payslip(env, status)
        res = env.client.delete(f"/api/v1/payroll/{payroll_id}",
                                headers=env.hdr("admin"))
        assert res.status_code == 409

    def test_a_draft_is_still_editable_and_deletable(self, env):
        payroll_id = self._payslip(env, PayrollStatusEnum.DRAFT)
        assert env.put("admin", f"/api/v1/payroll/{payroll_id}",
                       {"base_salary": 120000.0}).status_code == 200
        assert env.client.delete(f"/api/v1/payroll/{payroll_id}",
                                 headers=env.hdr("admin")).status_code == 204
