from pydantic import BaseModel
from typing import Optional
from datetime import datetime
from app.models.notification import NotificationTypeEnum, RelatedTypeEnum


class NotificationBase(BaseModel):
    title: str
    message: str
    type: NotificationTypeEnum = NotificationTypeEnum.INFO
    related_id: Optional[int] = None
    related_type: Optional[RelatedTypeEnum] = None


class NotificationCreate(NotificationBase):
    user_id: int


class NotificationResponse(BaseModel):
    id: int
    title: str
    message: str
    type: str
    read: bool
    createdAt: datetime
    relatedId: Optional[int] = None
    relatedType: Optional[str] = None
    
    class Config:
        from_attributes = True
        populate_by_name = True


class NotificationListResponse(BaseModel):
    success: bool = True
    data: list[NotificationResponse]


class MarkReadRequest(BaseModel):
    notification_id: int


class MarkReadResponse(BaseModel):
    success: bool = True
    message: str = "Notification marked as read"

