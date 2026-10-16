from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session, joinedload
from typing import List

from app.database import get_db
from app.models.announcement import Announcement
from app.models.user import User
from app.schemas.announcement import AnnouncementCreate, AnnouncementUpdate, AnnouncementResponse
from app.api.deps import get_current_user, require_manager
from app.models.notification import NotificationTypeEnum
from app.utils.notifications import notify_everyone

router = APIRouter()


def _to_response(announcement: Announcement) -> AnnouncementResponse:
    response = AnnouncementResponse.model_validate(announcement)
    if announcement.creator:
        response.creator_name = announcement.creator.full_name
    return response


@router.get("", response_model=List[AnnouncementResponse])
def list_announcements(
    include_inactive: bool = False,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List announcements, newest first. Inactive ones are admin/manager-only."""
    query = db.query(Announcement).options(joinedload(Announcement.creator))
    if include_inactive and current_user.role.value in ("admin", "manager"):
        pass  # admins/managers may view inactive announcements too
    else:
        query = query.filter(Announcement.is_active == True)  # noqa: E712
    announcements = query.order_by(Announcement.created_at.desc()).all()
    return [_to_response(a) for a in announcements]


@router.post("", response_model=AnnouncementResponse, status_code=status.HTTP_201_CREATED)
def create_announcement(
    data: AnnouncementCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_manager),
):
    """Create an announcement (Admin or Manager)"""
    announcement = Announcement(
        title=data.title,
        content=data.content,
        created_by=current_user.id,
    )
    db.add(announcement)
    db.commit()
    db.refresh(announcement)

    # Every active employee is notified. related_type is left unset because an
    # announcement is not one of the entities the notification dropdown links to.
    notify_everyone(
        db=db,
        title=f"Announcement: {announcement.title}",
        message=announcement.content,
        notification_type=NotificationTypeEnum.INFO,
        exclude_user_id=current_user.id,
    )

    return _to_response(announcement)


@router.put("/{announcement_id}", response_model=AnnouncementResponse)
def update_announcement(
    announcement_id: int,
    data: AnnouncementUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_manager),
):
    """Update an announcement (Admin or Manager)"""
    announcement = db.query(Announcement).filter(Announcement.id == announcement_id).first()
    if not announcement:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Announcement not found")

    if data.title is not None:
        announcement.title = data.title
    if data.content is not None:
        announcement.content = data.content
    if data.is_active is not None:
        announcement.is_active = data.is_active

    db.commit()
    db.refresh(announcement)
    return _to_response(announcement)


@router.delete("/{announcement_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_announcement(
    announcement_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_manager),
):
    """Delete an announcement (Admin or Manager)"""
    announcement = db.query(Announcement).filter(Announcement.id == announcement_id).first()
    if not announcement:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Announcement not found")

    db.delete(announcement)
    db.commit()
    return None
