from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime


# Base Team Schema
class TeamBase(BaseModel):
    name: str
    description: Optional[str] = None


# Team Create Schema
class TeamCreate(TeamBase):
    manager_id: Optional[int] = None


# Team Update Schema
class TeamUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    manager_id: Optional[int] = None


# Team Member Schema
class TeamMemberResponse(BaseModel):
    id: int
    full_name: str
    email: str
    joined_at: datetime
    
    class Config:
        from_attributes = True


# Team Response Schema
class TeamResponse(TeamBase):
    id: int
    manager_id: Optional[int] = None
    created_at: datetime
    updated_at: datetime
    
    class Config:
        from_attributes = True


# Team with Members Response
class TeamWithMembersResponse(TeamResponse):
    members: List[TeamMemberResponse] = []


# Add Member to Team Schema
class TeamMemberAdd(BaseModel):
    user_id: int

