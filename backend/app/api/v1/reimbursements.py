from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload
from typing import List, Optional
from app.database import get_db
from app.schemas.reimbursement import ReimbursementCreate, ReimbursementUpdate, ReimbursementResponse, ReimbursementApproval
from app.models.reimbursement import Reimbursement, ReimbursementStatusEnum
from app.models.user import User, RoleEnum
from app.api.deps import get_current_user, require_admin_or_hr, require_manager, is_privileged
from app.utils.notifications import create_reimbursement_notification, notify_admins
from app.utils.reimbursements import payment_route
from app.utils.email import send_reimbursement_approval_email
from app.models.notification import RelatedTypeEnum
from datetime import datetime

router = APIRouter()


def enrich_reimbursement_response(reimbursement: Reimbursement) -> ReimbursementResponse:
    """Helper function to enrich reimbursement response with employee and approver info"""
    reimbursement_dict = {
        **ReimbursementResponse.model_validate(reimbursement).model_dump(),
        "employee_name": reimbursement.employee.full_name if reimbursement.employee else None,
        "employee_email": reimbursement.employee.email if reimbursement.employee else None,
        "approver_name": reimbursement.approver.full_name if reimbursement.approver else None,
        # "payroll" when a payslip absorbed it, "separate" when it was handed
        # over outside payroll, "" while it is still unpaid. Without this the
        # screen can say "paid" but not how, which is the question somebody
        # asks right before paying it again.
        "paid_via": payment_route(reimbursement),
        "payroll_id": reimbursement.payroll_id,
    }
    return ReimbursementResponse(**reimbursement_dict)


@router.get("/", response_model=List[ReimbursementResponse])
def list_reimbursements(
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """List reimbursements - Employees see their own, Managers see subordinates', Admin sees all"""
    query = db.query(Reimbursement).options(
        joinedload(Reimbursement.employee),
        joinedload(Reimbursement.approver)
    )
    
    if is_privileged(current_user):
        reimbursements = query.offset(skip).limit(limit).all()
    elif current_user.role == RoleEnum.MANAGER:
        subordinate_ids = [s.id for s in current_user.subordinates] + [current_user.id]
        reimbursements = query.filter(
            Reimbursement.employee_id.in_(subordinate_ids)
        ).offset(skip).limit(limit).all()
    else:
        reimbursements = query.filter(
            Reimbursement.employee_id == current_user.id
        ).offset(skip).limit(limit).all()
    
    return [enrich_reimbursement_response(reimbursement) for reimbursement in reimbursements]


@router.get("/history", response_model=List[ReimbursementResponse])
def get_reimbursement_history(
    employee_id: Optional[int] = None,
    exclude_self: bool = False,
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Get reimbursement history - Employees see their own, Managers see subordinates',
    Admin sees all.

    exclude_self omits the caller's own claims, so an approver reviewing the
    team's history is not looking at their own claims mixed in. Applied after the
    role scoping below, so it can only ever narrow the result.
    """
    query = db.query(Reimbursement).options(
        joinedload(Reimbursement.employee),
        joinedload(Reimbursement.approver)
    )
    if exclude_self:
        query = query.filter(Reimbursement.employee_id != current_user.id)

    if is_privileged(current_user):
        # Admin can see all or filter by specific employee
        if employee_id:
            reimbursements = query.filter(Reimbursement.employee_id == employee_id).offset(skip).limit(limit).all()
        else:
            reimbursements = query.offset(skip).limit(limit).all()
    elif current_user.role == RoleEnum.MANAGER:
        # Managers can see their own and subordinates' history
        subordinate_ids = [s.id for s in current_user.subordinates] + [current_user.id]
        if employee_id:
            # Verify the employee_id is a subordinate or themselves
            if employee_id not in subordinate_ids:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="You can only view reimbursement history of your subordinates or yourself"
                )
            reimbursements = query.filter(Reimbursement.employee_id == employee_id).offset(skip).limit(limit).all()
        else:
            reimbursements = query.filter(Reimbursement.employee_id.in_(subordinate_ids)).offset(skip).limit(limit).all()
    else:
        # Employees can only see their own history
        if employee_id and employee_id != current_user.id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You can only view your own reimbursement history"
            )
        reimbursements = query.filter(Reimbursement.employee_id == current_user.id).offset(skip).limit(limit).all()
    
    return [enrich_reimbursement_response(reimbursement) for reimbursement in reimbursements]


@router.get("/pending", response_model=List[ReimbursementResponse])
def list_pending_reimbursements(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_manager)
):
    """List pending reimbursement requests (Manager/Admin only)
    Managers see only their subordinates' pending requests, not their own"""
    query = db.query(Reimbursement).options(
        joinedload(Reimbursement.employee),
        joinedload(Reimbursement.approver)
    )
    
    if is_privileged(current_user):
        reimbursements = query.filter(
            Reimbursement.status == ReimbursementStatusEnum.PENDING
        ).all()
    else:
        # Only get subordinates' pending reimbursements (exclude manager's own)
        subordinate_ids = [s.id for s in current_user.subordinates]
        reimbursements = query.filter(
            Reimbursement.employee_id.in_(subordinate_ids),
            Reimbursement.status == ReimbursementStatusEnum.PENDING
        ).all()
    
    return [enrich_reimbursement_response(reimbursement) for reimbursement in reimbursements]


@router.get("/{reimbursement_id:int}", response_model=ReimbursementResponse)
def get_reimbursement(
    reimbursement_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Get reimbursement by ID"""
    reimbursement = db.query(Reimbursement).options(joinedload(Reimbursement.employee)).filter(
        Reimbursement.id == reimbursement_id
    ).first()
    
    if not reimbursement:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Reimbursement not found"
        )
    
    # Check permissions
    if (is_privileged(current_user) or 
        reimbursement.employee_id == current_user.id or
        (current_user.role == RoleEnum.MANAGER and reimbursement.employee.manager_id == current_user.id)):
        return enrich_reimbursement_response(reimbursement)
    else:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not enough permissions"
        )


@router.post("/", response_model=ReimbursementResponse, status_code=status.HTTP_201_CREATED)
def create_reimbursement(
    reimbursement_data: ReimbursementCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Create a new reimbursement request"""
    # Same employee, same expense date, same category is the same real-world
    # expense being claimed twice — whatever amount or wording each attempt
    # used, since those are exactly the fields someone re-submitting (by
    # accident, or after tweaking the description) would change. Only a
    # rejected claim is excluded: it was decided against, so a genuine
    # resubmission for the same date/category should be allowed through.
    # There's no "cancelled" status on reimbursements to also exclude.
    duplicate = db.query(Reimbursement).filter(
        Reimbursement.employee_id == current_user.id,
        Reimbursement.reimbursement_type == reimbursement_data.reimbursement_type,
        func.date(Reimbursement.date_of_expense) == reimbursement_data.date_of_expense.date(),
        Reimbursement.status != ReimbursementStatusEnum.REJECTED,
    ).first()
    if duplicate:
        claim_type = getattr(reimbursement_data.reimbursement_type, "value", reimbursement_data.reimbursement_type)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"You already have a {claim_type} claim for "
                f"{reimbursement_data.date_of_expense.date().isoformat()} "
                f"({duplicate.status.value}). Edit that request, or use a different category or "
                f"date, instead of submitting another claim for the same expense."
            ),
        )

    reimbursement = Reimbursement(
        employee_id=current_user.id,
        reimbursement_type=reimbursement_data.reimbursement_type,
        amount=reimbursement_data.amount,
        description=reimbursement_data.description,
        date_of_expense=reimbursement_data.date_of_expense,
        receipt_url=reimbursement_data.receipt_url,
        status=ReimbursementStatusEnum.PENDING
    )

    db.add(reimbursement)
    db.commit()
    db.refresh(reimbursement)

    claim_type = getattr(reimbursement.reimbursement_type, "value", reimbursement.reimbursement_type)
    notify_admins(
        db=db,
        title="New reimbursement request",
        message=(
            f"{current_user.full_name} submitted a {claim_type} claim for "
            f"Rs. {reimbursement.amount:,.0f}."
        ),
        related_id=reimbursement.id,
        related_type=RelatedTypeEnum.REIMBURSEMENT,
        exclude_user_id=current_user.id,
    )

    # Reload with employee relationship
    reimbursement = db.query(Reimbursement).options(joinedload(Reimbursement.employee)).filter(
        Reimbursement.id == reimbursement.id
    ).first()
    return enrich_reimbursement_response(reimbursement)


@router.put("/{reimbursement_id:int}", response_model=ReimbursementResponse)
def update_reimbursement(
    reimbursement_id: int,
    reimbursement_data: ReimbursementUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Update reimbursement request (only if pending)"""
    reimbursement = db.query(Reimbursement).filter(Reimbursement.id == reimbursement_id).first()
    
    if not reimbursement:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Reimbursement not found"
        )
    
    if reimbursement.employee_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only update your own reimbursement requests"
        )
    
    if reimbursement.status != ReimbursementStatusEnum.PENDING:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Can only update pending reimbursement requests"
        )
    
    update_data = reimbursement_data.model_dump(exclude_unset=True)

    # Editing the type or the expense date can turn this claim into a
    # duplicate of another one just as easily as submitting a new claim can
    # — same check as create, against every *other* live claim.
    next_type = update_data.get("reimbursement_type", reimbursement.reimbursement_type)
    next_date = update_data.get("date_of_expense", reimbursement.date_of_expense)
    duplicate = db.query(Reimbursement).filter(
        Reimbursement.id != reimbursement.id,
        Reimbursement.employee_id == current_user.id,
        Reimbursement.reimbursement_type == next_type,
        func.date(Reimbursement.date_of_expense) == next_date.date(),
        Reimbursement.status != ReimbursementStatusEnum.REJECTED,
    ).first()
    if duplicate:
        claim_type = getattr(next_type, "value", next_type)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"You already have a {claim_type} claim for {next_date.date().isoformat()} "
                f"({duplicate.status.value}). Choose a different category or date."
            ),
        )

    for field, value in update_data.items():
        setattr(reimbursement, field, value)

    db.commit()
    db.refresh(reimbursement)
    
    # Reload with employee relationship
    reimbursement = db.query(Reimbursement).options(joinedload(Reimbursement.employee)).filter(
        Reimbursement.id == reimbursement.id
    ).first()
    return enrich_reimbursement_response(reimbursement)


@router.put("/{reimbursement_id:int}/approve", response_model=ReimbursementResponse)
def approve_or_reject_reimbursement(
    reimbursement_id: int,
    approval_data: ReimbursementApproval,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_manager)
):
    """Approve or reject reimbursement request (Manager/Admin only)"""
    reimbursement = db.query(Reimbursement).options(joinedload(Reimbursement.employee)).filter(
        Reimbursement.id == reimbursement_id
    ).first()
    
    if not reimbursement:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Reimbursement not found"
        )
    
    # Nobody signs off their own claim — not even an admin. An admin's own
    # reimbursement has to be decided by a different admin (HR), so the person
    # spending the money is never the person approving the payment.
    if reimbursement.employee_id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "You cannot approve or reject your own reimbursement request. "
                "Another admin has to review it."
            )
        )

    # Check authority (admin can approve everyone else's, managers only subordinates')
    if not is_privileged(current_user):
        # Only allow if it's a subordinate's reimbursement
        is_subordinate_reimbursement = (reimbursement.employee.manager_id == current_user.id 
                                        if reimbursement.employee else False)
        
        if not is_subordinate_reimbursement:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You can only approve/reject reimbursements of your subordinates"
            )
    
    if reimbursement.status != ReimbursementStatusEnum.PENDING:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Reimbursement request is not pending"
        )
    
    reimbursement.status = approval_data.status
    reimbursement.approved_by = current_user.id
    reimbursement.approved_at = datetime.utcnow()
    
    if approval_data.status == ReimbursementStatusEnum.REJECTED:
        reimbursement.rejection_reason = approval_data.rejection_reason
    
    db.commit()
    db.refresh(reimbursement)
    
    # Create notification for employee
    try:
        approver_name = current_user.full_name
        status_str = "approved" if approval_data.status == ReimbursementStatusEnum.APPROVED else "rejected"
        create_reimbursement_notification(
            db=db,
            user_id=reimbursement.employee_id,
            reimbursement_id=reimbursement.id,
            status=status_str,
            approver_name=approver_name
        )
    except Exception as e:
        # Don't fail the request if notification creation fails
        print(f"Failed to create notification: {e}")

    if reimbursement.employee and reimbursement.employee.email:
        background_tasks.add_task(
            send_reimbursement_approval_email,
            employee_email=reimbursement.employee.email,
            status=status_str,
            amount=reimbursement.amount,
        )

    # Reload with employee relationship
    reimbursement = db.query(Reimbursement).options(joinedload(Reimbursement.employee)).filter(
        Reimbursement.id == reimbursement.id
    ).first()
    return enrich_reimbursement_response(reimbursement)


def _detach_from_payroll(db: Session, reimbursement: Reimbursement) -> None:
    """
    Take an approved claim off the payslip that was going to carry it, so
    paying it by hand does not pay it twice.

    If that payslip has already gone out, it cannot be taken off — the money
    is with the employee. Saying so is the whole point: this is the last
    moment anybody looks before handing over cash for something salary has
    already covered.
    """
    from app.models.payroll import Payroll, PayrollStatusEnum

    if not reimbursement.payroll_id:
        return

    payroll = db.query(Payroll).filter(
        Payroll.id == reimbursement.payroll_id).first()
    if payroll is None:
        reimbursement.payroll_id = None
        return

    if payroll.status in (PayrollStatusEnum.PROCESSED, PayrollStatusEnum.PAID):
        period = f"{payroll.month:02d}/{payroll.year}"
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(f"This was already paid with the {period} salary "
                    f"(payslip #{payroll.id}), so it cannot be recorded as a "
                    f"separate payment. If it really was paid twice, correct "
                    f"that payslip instead."),
        )

    # Still a draft: drop the link so regenerating the payroll leaves it out.
    reimbursement.payroll_id = None


@router.put("/mark-paid", response_model=List[ReimbursementResponse])
def mark_many_as_paid(
    reimbursement_ids: List[int],
    db: Session = Depends(get_db),
    current_user: User = Depends(require_manager)
):
    """
    Mark several approved reimbursements as paid in one go (Manager/Admin).

    All or nothing. Settling a run of claims is one act of paying people, and
    a half-applied batch leaves nobody able to say which half — so if any one
    of them cannot be settled, none are.
    """
    if not reimbursement_ids:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="No reimbursements given")

    rows = (db.query(Reimbursement)
            .options(joinedload(Reimbursement.employee))
            .filter(Reimbursement.id.in_(reimbursement_ids)).all())
    found = {r.id for r in rows}
    missing = [i for i in reimbursement_ids if i not in found]
    if missing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Not found: {', '.join(str(i) for i in missing)}")

    not_approved = [r.id for r in rows
                    if r.status != ReimbursementStatusEnum.APPROVED]
    if not_approved:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=("Only approved reimbursements can be marked paid. "
                    f"Not approved: {', '.join(str(i) for i in not_approved)}"))

    now = datetime.utcnow()
    for row in rows:
        _detach_from_payroll(db, row)
        row.status = ReimbursementStatusEnum.PAID
        row.paid_at = now
    db.commit()

    for row in rows:
        db.refresh(row)
        create_reimbursement_notification(
            db=db, user_id=row.employee_id,
            reimbursement_id=row.id, status="paid")
    db.commit()

    return [enrich_reimbursement_response(r) for r in rows]


@router.put("/{reimbursement_id:int}/paid", response_model=ReimbursementResponse)
def mark_as_paid(
    reimbursement_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_manager)
):
    """Mark reimbursement as paid (Manager/Admin only)"""
    reimbursement = db.query(Reimbursement).filter(Reimbursement.id == reimbursement_id).first()
    
    if not reimbursement:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Reimbursement not found"
        )
    
    if reimbursement.status != ReimbursementStatusEnum.APPROVED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Can only mark approved reimbursements as paid"
        )

    _detach_from_payroll(db, reimbursement)

    reimbursement.status = ReimbursementStatusEnum.PAID
    reimbursement.paid_at = datetime.utcnow()

    db.commit()
    db.refresh(reimbursement)

    # The employee saw approve/reject notifications; without this one, the
    # only sign the money actually landed is noticing it on a later payslip.
    create_reimbursement_notification(
        db=db,
        user_id=reimbursement.employee_id,
        reimbursement_id=reimbursement.id,
        status="paid",
    )

    # Reload with employee relationship
    reimbursement = db.query(Reimbursement).options(joinedload(Reimbursement.employee)).filter(
        Reimbursement.id == reimbursement.id
    ).first()
    return enrich_reimbursement_response(reimbursement)


@router.delete("/{reimbursement_id:int}/purge", status_code=status.HTTP_204_NO_CONTENT)
def purge_reimbursement_record(
    reimbursement_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin_or_hr),
):
    """
    Permanently remove a finished reimbursement from the history (Admin only).

    DELETE /{reimbursement_id} only accepts pending claims, so it cannot clear
    history. This one can, with two guards:

    * A pending claim is refused — it still needs a decision.
    * A claim already carried onto a payslip (payroll_id set) is refused. Its
      amount is part of that payslip's totals, and api/v1/payslips refuses to
      publish figures that do not add up, so deleting the source line would
      leave the payslip unable to reconcile.
    """
    reimbursement = db.query(Reimbursement).filter(Reimbursement.id == reimbursement_id).first()
    if not reimbursement:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Reimbursement not found")

    claim_status = getattr(reimbursement.status, "value", reimbursement.status)
    if claim_status == ReimbursementStatusEnum.PENDING.value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only a finished claim can be deleted. Approve or reject this one first.",
        )

    if reimbursement.payroll_id is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "This claim has already been paid out on a payslip, so its amount is "
                "part of that payslip's totals. Deleting it would leave the payslip "
                "unable to reconcile, so it has to stay on record."
            ),
        )

    db.delete(reimbursement)
    db.commit()
    return None


@router.delete("/{reimbursement_id:int}", status_code=status.HTTP_204_NO_CONTENT)
def delete_reimbursement(
    reimbursement_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Delete reimbursement request (only if pending)"""
    reimbursement = db.query(Reimbursement).filter(Reimbursement.id == reimbursement_id).first()
    
    if not reimbursement:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Reimbursement not found"
        )
    
    if reimbursement.employee_id != current_user.id and not is_privileged(current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not enough permissions"
        )
    
    if reimbursement.status != ReimbursementStatusEnum.PENDING:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Can only delete pending reimbursement requests"
        )
    
    db.delete(reimbursement)
    db.commit()
    
    return None

