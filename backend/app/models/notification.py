from sqlalchemy import Column, Integer, String, ForeignKey, DateTime, Boolean, Text, Enum
from sqlalchemy.orm import relationship
from datetime import datetime
import enum
from app.database import Base


class NotificationTypeEnum(str, enum.Enum):
    SUCCESS = "success"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class RelatedTypeEnum(str, enum.Enum):
    LEAVE = "leave"
    REIMBURSEMENT = "reimbursement"
    PAYSLIP = "payslip"
    PAYSLIP_DISPUTE = "payslip_dispute"
    AWARD = "award"
    INVENTORY_ITEM = "inventory_item"


class Notification(Base):
    __tablename__ = "notifications"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    title = Column(String, nullable=False)
    message = Column(Text, nullable=False)
    # Use String instead of Enum to avoid case conversion issues
    # Store enum values as lowercase strings directly
    type = Column(String, default=NotificationTypeEnum.INFO.value, nullable=False)
    read = Column(Boolean, default=False, nullable=False, index=True)
    
    # Optional: Link to related entity (leave, reimbursement, etc.)
    related_id = Column(Integer, nullable=True)
    related_type = Column(String, nullable=True)  # Store as string to match database constraint
    
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    
    # Relationships
    user = relationship("User", back_populates="notifications")
    
    def __repr__(self):
        return f"<Notification {self.id} for user_id={self.user_id} ({self.type})>"

