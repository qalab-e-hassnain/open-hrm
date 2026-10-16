from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel
from typing import Optional
from sqlalchemy.orm import Session
from datetime import datetime, timedelta
from app.database import get_db
from app.schemas.user import UserCreate, UserResponse, Token, UserLogin
from app.models.user import User
from app.core.security import (create_access_token, create_refresh_token,
                               decode_access_token, get_password_hash,
                               new_token_id, verify_password)
from app.models.refresh_token import RefreshToken
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


def _client(request: Optional[Request]) -> tuple:
    """(user agent, ip) for the incident note, truncated and never trusted."""
    if request is None:
        return None, None
    agent = (request.headers.get("user-agent") or "")[:256] or None
    # The left-most X-Forwarded-For entry, when there is a proxy in front;
    # otherwise the peer address. Both are attacker-supplied as far as this
    # is concerned, which is why they are truncated and only ever read back
    # as part of an incident note.
    forwarded = request.headers.get("x-forwarded-for") or ""
    if forwarded:
        address = forwarded.split(",")[0].strip()
    elif request.client:
        address = request.client.host or ""
    else:
        address = ""
    return agent, address[:64] or None


def revoke_family(db: Session, family_id: str, reason: str) -> int:
    """
    End every live token in one chain. Returns how many were still live.

    Used for signing out, and for the reuse case where the safe answer is to
    end the session rather than guess which of the two holders is the real
    user.
    """
    live = (db.query(RefreshToken)
            .filter(RefreshToken.family_id == family_id,
                    RefreshToken.revoked_at.is_(None))
            .all())
    now = datetime.utcnow()
    for token in live:
        token.revoked_at = now
        token.revoked_reason = reason
    return len(live)


def revoke_all_sessions(db: Session, user_id: int, reason: str) -> int:
    """
    Every live refresh token this user holds, on every device.

    Called when an account is deactivated or its password is reset. Without
    it, taking somebody's access away leaves their phone able to mint fresh
    access tokens for another week, which is the gap between "we removed
    their account" and "they can no longer read anything".
    """
    live = (db.query(RefreshToken)
            .filter(RefreshToken.user_id == user_id,
                    RefreshToken.revoked_at.is_(None))
            .all())
    now = datetime.utcnow()
    for token in live:
        token.revoked_at = now
        token.revoked_reason = reason
    return len(live)


def issue_refresh_token(db: Session, user: User, *,
                        request: Optional[Request] = None,
                        family_id: Optional[str] = None,
                        family_started_at: Optional[datetime] = None) -> str:
    """
    Mint a refresh token and record it, in the caller's transaction.

    A new sign-in starts a new family. A rotation continues the one it was
    given, carrying the original start time forward so the ceiling on how
    long a chain may run is measured from the first sign-in rather than the
    last refresh.
    """
    jti = new_token_id()
    family = family_id or new_token_id()
    started = family_started_at or datetime.utcnow()
    agent, address = _client(request)

    token = create_refresh_token({"sub": user.email}, jti=jti, family_id=family)
    db.add(RefreshToken(
        jti=jti,
        family_id=family,
        user_id=user.id,
        issued_at=datetime.utcnow(),
        expires_at=datetime.utcnow() + timedelta(
            days=settings.REFRESH_TOKEN_EXPIRE_DAYS),
        family_started_at=started,
        user_agent=agent,
        ip_address=address,
    ))
    return token


def prune_expired_refresh_tokens(db: Session, *, keep_days: int = 30) -> int:
    """
    Delete rows for tokens that expired long enough ago to be of no use.

    Kept for a while after expiry on purpose: a reuse attempt against a
    recently expired token is worth being able to see. Called from the cron
    sweep.
    """
    cutoff = datetime.utcnow() - timedelta(days=keep_days)
    deleted = (db.query(RefreshToken)
               .filter(RefreshToken.expires_at < cutoff)
               .delete(synchronize_session=False))
    return deleted


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

    refresh_token = issue_refresh_token(db, user, request=request)
    db.commit()

    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
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

    refresh_token = None
    if user_data.remember_device:
        refresh_token = issue_refresh_token(db, user, request=request)
    db.commit()

    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
        "user": user
    }


# Must live at module level so the /refresh route below can reference it.
class RefreshRequest(BaseModel):
    refresh_token: str


# One message for every way a refresh can fail. Which reason applies is
# recorded server-side; telling the caller whether a token was unknown,
# superseded or merely expired would let somebody probe the difference.
_REFRESH_REFUSED = "Session expired. Please sign in again."


@router.post("/refresh", response_model=Token)
def refresh(body: RefreshRequest, request: Request, db: Session = Depends(get_db)):
    """
    Exchange a refresh token for a new pair, and invalidate the one used.

    The token presented must be the newest link in its chain. Presenting an
    older one means two parties hold tokens from the same chain, so the whole
    family is revoked — see app/models/refresh_token.py for why that is the
    response to both the innocent and the guilty explanation.
    """
    payload = decode_access_token(body.refresh_token)
    if not payload or payload.get("type") != "refresh":
        raise HTTPException(401, _REFRESH_REFUSED)

    jti = payload.get("jti")
    if not jti:
        # Issued before refresh tokens were tracked. There is no family to
        # rotate within and no way to tell a stolen one from a live one, so
        # it buys a sign-in rather than a silent upgrade.
        raise HTTPException(401, _REFRESH_REFUSED)

    record = db.query(RefreshToken).filter(RefreshToken.jti == jti).first()
    if record is None:
        # Correctly signed and unknown to us: pruned long after expiry, or
        # minted by somebody holding SECRET_KEY. Either way, not a session.
        audit.record(db, "auth.refresh.unknown_token",
                     detail=f"jti={jti[:12]}", request=request)
        db.commit()
        raise HTTPException(401, _REFRESH_REFUSED)

    user = db.query(User).filter(User.id == record.user_id).first()

    if record.revoked_at is not None:
        # The one case this table exists for.
        revoked = revoke_family(db, record.family_id, "reuse_detected")
        audit.record(
            db, "auth.refresh.reuse_detected", actor=user,
            detail=(f"Token {jti[:12]} was already {record.revoked_reason}; "
                    f"revoked {revoked} live token(s) in family "
                    f"{record.family_id[:12]}. Two parties held tokens from "
                    f"one session."),
            request=request)
        db.commit()
        raise HTTPException(401, _REFRESH_REFUSED)

    now = datetime.utcnow()
    if record.expires_at <= now:
        raise HTTPException(401, _REFRESH_REFUSED)

    # Refreshing must not extend a session for ever.
    ceiling = record.family_started_at + timedelta(
        days=settings.REFRESH_TOKEN_FAMILY_MAX_DAYS)
    if now >= ceiling:
        revoke_family(db, record.family_id, "family_expired")
        db.commit()
        raise HTTPException(401, _REFRESH_REFUSED)

    if not user or not user.is_active:
        revoke_family(db, record.family_id, "user_deactivated")
        db.commit()
        raise HTTPException(401, _REFRESH_REFUSED)

    # Rotate: the presented token is spent, and its successor continues the
    # family from the same original start time.
    successor = issue_refresh_token(
        db, user, request=request, family_id=record.family_id,
        family_started_at=record.family_started_at)
    record.revoked_at = now
    record.revoked_reason = "rotated"
    record.replaced_by = decode_access_token(successor).get("jti")
    db.commit()

    return Token(
        access_token=create_access_token({"sub": user.email},
                    timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)),
        refresh_token=successor,
        user=user,
    )


class LogoutRequest(BaseModel):
    # Optional so that a client which never asked to be remembered can still
    # call this without inventing a token.
    refresh_token: Optional[str] = None


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(body: LogoutRequest, request: Request, db: Session = Depends(get_db)):
    """
    End the session the given refresh token belongs to.

    Signing out used to be entirely a client-side act: the portal dropped the
    tokens and the server was never told, so the refresh token it had handed
    out kept working for the rest of its week. Anyone who had copied it — or
    anyone using the machine afterwards, if the browser had kept it — could
    carry on.

    Returns 204 whether or not the token turns out to be one we know, because
    the caller is signing out either way and the difference is not theirs to
    learn. The access token already issued is not revoked; it is short-lived
    by design, and tracking individual access tokens would mean a database
    round trip on every request.
    """
    if not body.refresh_token:
        return None

    payload = decode_access_token(body.refresh_token)
    if not payload or payload.get("type") != "refresh" or not payload.get("jti"):
        return None

    record = (db.query(RefreshToken)
              .filter(RefreshToken.jti == payload["jti"]).first())
    if record is None:
        return None

    revoke_family(db, record.family_id, "signed_out")
    db.commit()
    return None

