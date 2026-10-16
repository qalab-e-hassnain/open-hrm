"""
Utility functions for creating notifications
"""
import calendar
from typing import List, Optional

from sqlalchemy.orm import Session
from app.models.notification import Notification, NotificationTypeEnum, RelatedTypeEnum


def queue_payslip_notification(
    db: Session,
    user_id: int,
    payroll_id: int,
    month: int,
    year: int,
) -> Notification:
    """Add a payslip-ready notification to the caller's transaction."""
    notification = Notification(
        user_id=user_id,
        title="Payslip generated",
        message=f"Your payslip for {calendar.month_name[month]} {year} is ready to view.",
        type=NotificationTypeEnum.SUCCESS.value,
        related_id=payroll_id,
        related_type=RelatedTypeEnum.PAYSLIP.value,
    )
    db.add(notification)
    return notification


def create_notification(
    db: Session,
    user_id: int,
    title: str,
    message: str,
    notification_type: NotificationTypeEnum = NotificationTypeEnum.INFO,
    related_id: int = None,
    related_type: RelatedTypeEnum = None
) -> Notification:
    """
    Create a notification for a user
    
    Args:
        db: Database session
        user_id: ID of the user to notify
        title: Notification title
        message: Notification message
        notification_type: Type of notification (success, info, warning, error)
        related_id: Optional ID of related entity (leave, reimbursement, etc.)
        related_type: Optional type of related entity
    
    Returns:
        Created Notification object
    """
    # Ensure enum values are used (not enum names) for database storage
    # When native_enum=False, SQLAlchemy should use values, but let's be explicit
    # Convert enum objects to their string values
    type_value = notification_type
    if hasattr(notification_type, 'value'):
        type_value = notification_type.value
    elif isinstance(notification_type, str):
        type_value = notification_type.lower()
    
    related_type_value = related_type
    if related_type:
        if hasattr(related_type, 'value'):
            related_type_value = related_type.value
        elif isinstance(related_type, str):
            related_type_value = related_type.lower()
        # If it's still an enum object, try to get value
        elif hasattr(related_type, 'name'):
            # It's an enum, get the value
            related_type_value = related_type.value
    
    notification = Notification(
        user_id=user_id,
        title=title,
        message=message,
        type=type_value,
        related_id=related_id,
        related_type=related_type_value
    )
    
    db.add(notification)
    db.commit()
    db.refresh(notification)
    
    return notification


def notify_users(
    db: Session,
    user_ids: List[int],
    title: str,
    message: str,
    notification_type: NotificationTypeEnum = NotificationTypeEnum.INFO,
    related_id: int = None,
    related_type: RelatedTypeEnum = None,
    exclude_user_id: Optional[int] = None,
) -> List[Notification]:
    """
    Send the same notification to several users in one transaction.

    `exclude_user_id` skips the person who caused the event, so an admin who
    approves their own request or posts an announcement is not notified about it.
    """
    type_value = getattr(notification_type, "value", notification_type)
    related_type_value = getattr(related_type, "value", related_type)

    recipients = {uid for uid in user_ids if uid and uid != exclude_user_id}
    if not recipients:
        return []

    notifications = [
        Notification(
            user_id=user_id,
            title=title,
            message=message,
            type=type_value,
            related_id=related_id,
            related_type=related_type_value,
        )
        for user_id in sorted(recipients)
    ]
    db.add_all(notifications)
    db.commit()
    return notifications


def notify_admins(
    db: Session,
    title: str,
    message: str,
    notification_type: NotificationTypeEnum = NotificationTypeEnum.INFO,
    related_id: int = None,
    related_type: RelatedTypeEnum = None,
    exclude_user_id: Optional[int] = None,
) -> List[Notification]:
    """
    Notify every active admin — used for anything an employee changes that HR
    needs to know about (requests raised, attendance activity).

    Never raises: a failure to notify must not roll back the action that
    triggered it, so the caller's request still succeeds.
    """
    from sqlalchemy import or_
    from app.models.user import RoleEnum, User

    try:
        # HR managers carry admin-level responsibility, so they receive the same
        # operational notifications admins do.
        admin_ids = [
            row[0] for row in db.query(User.id).filter(
                or_(User.role == RoleEnum.ADMIN, User.is_hr == True),  # noqa: E712
                User.is_active == True,  # noqa: E712
            ).all()
        ]
        return notify_users(
            db=db,
            user_ids=admin_ids,
            title=title,
            message=message,
            notification_type=notification_type,
            related_id=related_id,
            related_type=related_type,
            exclude_user_id=exclude_user_id,
        )
    except Exception as exc:  # pragma: no cover - notification is never critical
        print(f"⚠️ Failed to notify admins: {exc}")
        db.rollback()
        return []


def notify_everyone(
    db: Session,
    title: str,
    message: str,
    notification_type: NotificationTypeEnum = NotificationTypeEnum.INFO,
    related_id: int = None,
    related_type: RelatedTypeEnum = None,
    exclude_user_id: Optional[int] = None,
) -> List[Notification]:
    """
    Notify every active user — used for company-wide announcements.

    Never raises, for the same reason as notify_admins().
    """
    from app.models.user import User

    try:
        user_ids = [
            row[0] for row in db.query(User.id).filter(
                User.is_active == True,  # noqa: E712
            ).all()
        ]
        return notify_users(
            db=db,
            user_ids=user_ids,
            title=title,
            message=message,
            notification_type=notification_type,
            related_id=related_id,
            related_type=related_type,
            exclude_user_id=exclude_user_id,
        )
    except Exception as exc:  # pragma: no cover - notification is never critical
        print(f"⚠️ Failed to notify all users: {exc}")
        db.rollback()
        return []


def create_leave_notification(
    db: Session,
    user_id: int,
    leave_id: int,
    status: str,
    approver_name: str = None
) -> Notification:
    """
    Create a notification for leave approval/rejection
    
    Args:
        db: Database session
        user_id: ID of the employee who requested leave
        leave_id: ID of the leave request
        status: Leave status (approved, rejected, manager_approved)
        approver_name: Name of the person who approved/rejected
    
    Returns:
        Created Notification object
    """
    if status == "approved":
        title = "Leave Approved"
        message = f"Your leave request has been approved"
        if approver_name:
            message += f" by {approver_name}"
        notification_type = NotificationTypeEnum.SUCCESS
    elif status == "manager_approved":
        title = "Leave Approved by Manager"
        message = f"Your leave request has been approved by your manager"
        if approver_name:
            message += f" ({approver_name})"
        message += ". Waiting for HR/Admin final approval."
        notification_type = NotificationTypeEnum.INFO
    elif status == "rejected":
        title = "Leave Rejected"
        message = f"Your leave request has been rejected"
        if approver_name:
            message += f" by {approver_name}"
        notification_type = NotificationTypeEnum.ERROR
    elif status == "cancelled":
        title = "Leave Cancelled"
        message = "Your leave request has been cancelled"
        if approver_name:
            message += f" by {approver_name}"
        notification_type = NotificationTypeEnum.WARNING
    else:
        title = "Leave Status Updated"
        message = f"Your leave request status has been updated to {status}"
        notification_type = NotificationTypeEnum.INFO
    
    return create_notification(
        db=db,
        user_id=user_id,
        title=title,
        message=message,
        notification_type=notification_type,
        related_id=leave_id,
        related_type=RelatedTypeEnum.LEAVE
    )


def create_reimbursement_notification(
    db: Session,
    user_id: int,
    reimbursement_id: int,
    status: str,
    approver_name: str = None
) -> Notification:
    """
    Create a notification for reimbursement approval/rejection
    
    Args:
        db: Database session
        user_id: ID of the employee who requested reimbursement
        reimbursement_id: ID of the reimbursement request
        status: Reimbursement status (approved, rejected)
        approver_name: Name of the person who approved/rejected
    
    Returns:
        Created Notification object
    """
    if status == "approved":
        title = "Reimbursement Approved"
        message = f"Your reimbursement request has been approved"
        if approver_name:
            message += f" by {approver_name}"
        notification_type = NotificationTypeEnum.SUCCESS
    elif status == "rejected":
        title = "Reimbursement Rejected"
        message = f"Your reimbursement request has been rejected"
        if approver_name:
            message += f" by {approver_name}"
        notification_type = NotificationTypeEnum.ERROR
    elif status == "paid":
        title = "Reimbursement Paid"
        message = "Your reimbursement has been marked as paid."
        notification_type = NotificationTypeEnum.SUCCESS
    else:
        title = "Reimbursement Status Updated"
        message = f"Your reimbursement request status has been updated to {status}"
        notification_type = NotificationTypeEnum.INFO
    
    return create_notification(
        db=db,
        user_id=user_id,
        title=title,
        message=message,
        notification_type=notification_type,
        related_id=reimbursement_id,
        related_type=RelatedTypeEnum.REIMBURSEMENT
    )

