from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List
from app.database import get_db
from app.schemas.team import TeamCreate, TeamUpdate, TeamResponse, TeamMemberAdd, TeamWithMembersResponse
from app.models.team import Team, UserTeam
from app.models.user import User, RoleEnum
from app.api.deps import get_current_user, require_admin_or_hr, require_manager, is_privileged

router = APIRouter()


@router.get("/", response_model=List[TeamResponse])
def list_teams(
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """List all teams"""
    teams = db.query(Team).offset(skip).limit(limit).all()
    return teams


@router.get("/{team_id}", response_model=TeamWithMembersResponse)
def get_team(
    team_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Get team by ID with members"""
    team = db.query(Team).filter(Team.id == team_id).first()
    
    if not team:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Team not found"
        )
    
    # Get team members
    members = []
    for user_team in team.members:
        members.append({
            "id": user_team.user.id,
            "full_name": user_team.user.full_name,
            "email": user_team.user.email,
            "joined_at": user_team.joined_at
        })
    
    return {
        "id": team.id,
        "name": team.name,
        "description": team.description,
        "manager_id": team.manager_id,
        "created_at": team.created_at,
        "updated_at": team.updated_at,
        "members": members
    }


@router.post("/", response_model=TeamResponse, status_code=status.HTTP_201_CREATED)
def create_team(
    team_data: TeamCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin_or_hr)
):
    """Create a new team (Admin only)"""
    # Check if team name already exists
    existing_team = db.query(Team).filter(Team.name == team_data.name).first()
    if existing_team:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Team name already exists"
        )
    
    # Verify manager exists and has manager role
    if team_data.manager_id:
        manager = db.query(User).filter(User.id == team_data.manager_id).first()
        if not manager:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Manager not found"
            )
        if manager.role not in [RoleEnum.MANAGER, RoleEnum.ADMIN]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="User must have manager or admin role"
            )
    
    team = Team(
        name=team_data.name,
        description=team_data.description,
        manager_id=team_data.manager_id
    )
    
    db.add(team)
    db.commit()
    db.refresh(team)
    
    return team


@router.put("/{team_id}", response_model=TeamResponse)
def update_team(
    team_id: int,
    team_data: TeamUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin_or_hr)
):
    """Update team (Admin only)"""
    team = db.query(Team).filter(Team.id == team_id).first()
    
    if not team:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Team not found"
        )
    
    # Update team fields
    update_data = team_data.model_dump(exclude_unset=True)
    
    # Verify manager if being updated
    if "manager_id" in update_data and update_data["manager_id"]:
        manager = db.query(User).filter(User.id == update_data["manager_id"]).first()
        if not manager:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Manager not found"
            )
        if manager.role not in [RoleEnum.MANAGER, RoleEnum.ADMIN]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="User must have manager or admin role"
            )
    
    for field, value in update_data.items():
        setattr(team, field, value)
    
    db.commit()
    db.refresh(team)
    
    return team


@router.delete("/{team_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_team(
    team_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin_or_hr)
):
    """Delete team (Admin only)"""
    team = db.query(Team).filter(Team.id == team_id).first()
    
    if not team:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Team not found"
        )
    
    db.delete(team)
    db.commit()
    
    return None


@router.post("/{team_id}/members", status_code=status.HTTP_201_CREATED)
def add_team_member(
    team_id: int,
    member_data: TeamMemberAdd,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_manager)
):
    """Add member to team (Manager/Admin only)"""
    # Verify team exists
    team = db.query(Team).filter(Team.id == team_id).first()
    if not team:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Team not found"
        )
    
    # Check if user is the team manager or admin
    if not is_privileged(current_user) and team.manager_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only team manager or admin can add members"
        )
    
    # Verify user exists
    user = db.query(User).filter(User.id == member_data.user_id).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )
    
    # Check if user is already in the team
    existing = db.query(UserTeam).filter(
        UserTeam.user_id == member_data.user_id,
        UserTeam.team_id == team_id
    ).first()
    
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User is already a member of this team"
        )
    
    # Add user to team
    user_team = UserTeam(user_id=member_data.user_id, team_id=team_id)
    db.add(user_team)
    db.commit()
    
    return {"message": "Member added successfully"}


@router.delete("/{team_id}/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_team_member(
    team_id: int,
    user_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_manager)
):
    """Remove member from team (Manager/Admin only)"""
    # Verify team exists
    team = db.query(Team).filter(Team.id == team_id).first()
    if not team:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Team not found"
        )
    
    # Check if user is the team manager or admin
    if not is_privileged(current_user) and team.manager_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only team manager or admin can remove members"
        )
    
    # Find and delete the user-team relationship
    user_team = db.query(UserTeam).filter(
        UserTeam.user_id == user_id,
        UserTeam.team_id == team_id
    ).first()
    
    if not user_team:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User is not a member of this team"
        )
    
    db.delete(user_team)
    db.commit()
    
    return None


@router.get("/my/teams", response_model=List[TeamResponse])
def get_my_teams(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Get teams that the current user is a member of"""
    user_teams = db.query(UserTeam).filter(UserTeam.user_id == current_user.id).all()
    teams = [ut.team for ut in user_teams]
    return teams

