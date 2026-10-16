from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel
from sqlalchemy.orm import Session
from datetime import timedelta
from app.database import get_db
from app.schemas.user import UserCreate, UserResponse, Token, UserLogin
from app.models.user import User
from app.core.security import create_refresh_token, decode_access_token, verify_password, get_password_hash, create_access_token
from app.config import settings
from app.utils import audit

# There is no self-service password reset. A user who forgets their password
# contacts an admin, who sets a new one from User Management
# (POST /users/{user_id}/reset-password in api/v1/users.py).

router = APIRouter()

# Public self-registration was removed deliberately. It accepted `role` and
# `is_hr` straight from an unauthenticated request body, so anyone able to
# reach the API could mint themselves an administrator. Staff accounts are
# created by an admin/HR through POST /api/v1/users/, which authenticates the
# caller and runs assert_can_grant_role() before any role is assigned.


def _reject_if_locked(db: Session, email: str, request: Request) -> None:
    """
    Stop credential stuffing before the password is even checked.

    Counted per email over a rolling window (see app.utils.audit), so an
    attacker spraying one account locks that account rather than the service,
    and an employee's own typos age out without an admin having to intervene.
    """
    if audit.is_locked_out(db, email):
        audit.record(db, "login.blocked", actor_email=email, request=request)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                f"Too many failed sign-in attempts. Try again in "
                f"{int(audit.LOCKOUT_WINDOW.total_seconds() // 60)} minutes."
            ),
        )


def _record_failure(db: Session, email: str, request: Request, reason: str) -> None:
    audit.record(db, "login.failed", actor_email=email, request=request,
                 detail={"reason": reason})


@router.post("/login", response_model=Token)
def login(
    request: Request,
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db)
):
    """Login and get access token"""
    email = form_data.username
    _reject_if_locked(db, email, request)

    user = db.query(User).filter(User.email == email).first()

    if not user or not verify_password(form_data.password, user.hashed_password):
        _record_failure(db, email, request,
                        "unknown_email" if not user else "bad_password")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not user.is_active:
        _record_failure(db, email, request, "inactive")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Inactive user"
        )

    audit.record(db, "login.success", actor=user, request=request)
    
    # Create access token
    access_token_expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": user.email},
        expires_delta=access_token_expires
    )

    return {
        "access_token": access_token,
        "refresh_token": create_refresh_token(data={"sub": user.email}),
        "token_type": "bearer",
        "user": user
    }


@router.post("/login-json", response_model=Token)
def login_json(user_data: UserLogin, request: Request, db: Session = Depends(get_db)):
    """Login with JSON body (alternative to form data)"""
    email = user_data.email
    _reject_if_locked(db, email, request)

    user = db.query(User).filter(User.email == email).first()

    if not user or not verify_password(user_data.password, user.hashed_password):
        _record_failure(db, email, request,
                        "unknown_email" if not user else "bad_password")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password"
        )

    if not user.is_active:
        _record_failure(db, email, request, "inactive")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Inactive user"
        )

    audit.record(db, "login.success", actor=user, request=request)
    
    access_token_expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": user.email},
        expires_delta=access_token_expires
    )

    return {
        "access_token": access_token,
        "refresh_token": create_refresh_token(data={"sub": user.email}) if user_data.remember_device else None,
        "token_type": "bearer",
        "user": user
    }


# Must live at module level so the /refresh route below can reference it.
class RefreshRequest(BaseModel):
    refresh_token: str


@router.post("/refresh", response_model=Token)
def refresh(body: RefreshRequest, db: Session = Depends(get_db)):
    payload = decode_access_token(body.refresh_token)
    if not payload or payload.get("type") != "refresh":
        raise HTTPException(401, "Invalid refresh token")
    user = db.query(User).filter(User.email == payload.get("sub")).first()
    if not user or not user.is_active:
        raise HTTPException(401, "Invalid refresh token")
    return Token(
        access_token=create_access_token({"sub": user.email},
                    timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)),
        refresh_token=create_refresh_token({"sub": user.email}),
        user=user,
    )

