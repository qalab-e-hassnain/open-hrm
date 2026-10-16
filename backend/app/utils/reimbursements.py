"""
Settling a reimbursement, and keeping it from being paid twice.

An approved reimbursement leaves the company one of two ways: swept into the
month's payslip as an earning, or handed over separately — cash, a transfer,
whatever happened at the desk. Both are "paid"; the difference is which one,
and the register has to hold the answer, because the only thing standing
between a claim and being paid for it twice is knowing it has already gone.

`payroll_id` carries that: set means a payslip absorbed it, null on a paid row
means it was settled outside payroll.
"""
import logging
from datetime import datetime

from sqlalchemy.orm import Session

from app.models.reimbursement import Reimbursement, ReimbursementStatusEnum

logger = logging.getLogger(__name__)


def payment_route(reimbursement: Reimbursement) -> str:
    """
    How this one was paid, for display: "payroll", "separate", or "" if it has
    not been paid yet.
    """
    if reimbursement.status != ReimbursementStatusEnum.PAID:
        return ""
    return "payroll" if reimbursement.payroll_id else "separate"


def carried_by(db: Session, payroll) -> list:
    """Approved or paid reimbursements this payslip absorbed."""
    return (db.query(Reimbursement)
            .filter(Reimbursement.payroll_id == payroll.id)
            .all())


def settle_with_payroll(db: Session, payroll, paid_at: datetime = None) -> int:
    """
    Mark the reimbursements a payslip absorbed as paid, when that payslip is.

    Without this they sit at "approved" forever: the employee has the money in
    their salary but the claim still reads as outstanding, so it turns up on
    every "what do we owe" list and invites somebody to pay it a second time
    in cash. Returns how many were settled.
    """
    settled = 0
    for row in carried_by(db, payroll):
        if row.status != ReimbursementStatusEnum.APPROVED:
            continue
        row.status = ReimbursementStatusEnum.PAID
        # It was paid when the salary was. If that payslip records no payment
        # date, this keeps none either, rather than borrowing today's — the
        # same rule payroll follows. "Paid, date not recorded" is true;
        # "paid on 16 September" on an August salary is not.
        row.paid_at = paid_at or payroll.payment_date
        settled += 1
    if settled:
        logger.info("Settled %d reimbursement(s) with payroll %s", settled, payroll.id)
    return settled
