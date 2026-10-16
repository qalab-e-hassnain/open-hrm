from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List
from app.database import get_db
from app.models.notification import Notification, NotificationTypeEnum, RelatedTypeEnum
from app.models.user import User
from app.schemas.notification import (
    NotificationResponse, 
    NotificationListResponse,
    MarkReadRequest,
    MarkReadResponse
)
from app.api.deps import get_current_user, is_privileged
from app.utils.celebrations import sweep_celebrations
from datetime import datetime

router = APIRouter()


def format_notification_response(notification: Notification) -> NotificationResponse:
    """Format notification for API response"""
    # notification.type and notification.related_type are already strings (not enum objects)
    # since we use String columns in the model
    return NotificationResponse(
        id=notification.id,
        title=notification.title,
        message=notification.message,
        type=notification.type,  # Already a string
        read=notification.read,
        createdAt=notification.created_at,
        relatedId=notification.related_id,
        relatedType=notification.related_type  # Already a string or None
    )


@router.get("/", response_model=NotificationListResponse)
def get_notifications(
    skip: int = 0,
    limit: int = 100,
    unread_only: bool = False,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Get notifications for the current user
    - Returns all notifications or unread only based on query parameter
    - Ordered by created_at descending (newest first)
    """
    try:
        # No scheduler in this stack, so today's birthdays and anniversaries are
        # raised here — the people who receive them are the ones reading this
        # list. Idempotent per person per year, and it never raises.
        if is_privileged(current_user):
            sweep_celebrations(db)

        query = db.query(Notification).filter(Notification.user_id == current_user.id)

        if unread_only:
            query = query.filter(Notification.read == False)
        
        notifications = query.order_by(Notification.created_at.desc()).offset(skip).limit(limit).all()
        
        return NotificationListResponse(
            success=True,
            data=[format_notification_response(notif) for notif in notifications]
        )
    except Exception as e:
        # Check if it's a table doesn't exist error
        error_msg = str(e).lower()
        if "does not exist" in error_msg or "relation" in error_msg:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Notifications table not found. Please run database migration: python fix_database.py"
            )
        # Re-raise other errors
        raise


@router.get("/unread-count", response_model=dict)
def get_unread_count(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Get count of unread notifications for the current user"""
    try:
        count = db.query(Notification).filter(
            Notification.user_id == current_user.id,
            Notification.read == False
        ).count()
        
        return {
            "success": True,
            "count": count
        }
    except Exception as e:
        # If table doesn't exist, return 0 count
        error_msg = str(e).lower()
        if "does not exist" in error_msg or "relation" in error_msg:
            return {
                "success": True,
                "count": 0
            }
        raise


@router.post("/mark-read", response_model=MarkReadResponse)
def mark_notification_read(
    request: MarkReadRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Mark a specific notification as read
    """
    notification = db.query(Notification).filter(
        Notification.id == request.notification_id,
        Notification.user_id == current_user.id
    ).first()
    
    if not notification:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Notification not found"
        )
    
    if notification.read:
        return MarkReadResponse(
            success=True,
            message="Notification is already marked as read"
        )
    
    notification.read = True
    db.commit()
    db.refresh(notification)
    
    return MarkReadResponse(
        success=True,
        message="Notification marked as read"
    )


@router.post("/mark-all-read", response_model=dict)
def mark_all_notifications_read(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Mark all notifications as read for the current user
    """
    updated = db.query(Notification).filter(
        Notification.user_id == current_user.id,
        Notification.read == False
    ).update({"read": True})
    
    db.commit()
    
    return {
        "success": True,
        "message": f"Marked {updated} notifications as read",
        "count": updated
    }


@router.delete("/{notification_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_notification(
    notification_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Delete a notification (soft delete by marking as read, or hard delete)
    """
    notification = db.query(Notification).filter(
        Notification.id == notification_id,
        Notification.user_id == current_user.id
    ).first()
    
    if not notification:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Notification not found"
        )
    
    db.delete(notification)
    db.commit()
    
    return None

