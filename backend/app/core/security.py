import secrets
from datetime import datetime, timedelta
from typing import Optional
# PyJWT rather than python-jose.
#
# python-jose is effectively unmaintained and carries an advisory with no
# fix: it accepts DER-encoded public keys during HMAC initialisation, so an
# attacker holding the service's public key can forge HS256 tokens "when
# algorithms are not explicitly restricted". They are restricted below, and
# this deployment signs with a symmetric secret rather than a key pair, so
# the hole was not reachable here — but the advisory is itself described as
# an incomplete fix for an earlier CVE, and that is not a library to keep
# underneath the only thing standing between a stranger and everyone's
# salary.
#
# It also drags in `ecdsa`, whose maintainers state that side-channel
# attacks are out of scope and that the Minerva timing attack on P-256 will
# not be fixed. Nothing here uses ECDSA; the dependency left with jose.
import jwt
from passlib.context import CryptContext
from app.config import settings

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a password against a hash"""
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    """Hash a password"""
    return pwd_context.hash(password)


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """Create JWT access token"""
    to_encode = data.copy()
    to_encode.update({"type": "access"})
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        # ACCESS_TOKEN_EXPIRE_MINUTES is the setting that exists; the old
        # ACCESS_TOKEN_EXPIRE_DAYS raised AttributeError for any caller that
        # did not pass an explicit delta.
        expire = datetime.utcnow() + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)

    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)
    return encoded_jwt

def new_token_id() -> str:
    """An unguessable id for one refresh token, or for a family of them."""
    return secrets.token_urlsafe(24)


def create_refresh_token(data: dict, *, jti: Optional[str] = None,
                         family_id: Optional[str] = None) -> str:
    """
    Mint a refresh token.

    `jti` identifies this token and `fid` the chain it belongs to. Both are
    recorded server-side (see app/models/refresh_token.py) so that a token
    can be revoked, and so that presenting a superseded one is detectable
    rather than indistinguishable from normal use.

    They default to fresh random values, which keeps the signature compatible
    with callers that only want a signed token — the tests that check claim
    shape, mainly. Issuing one for an actual session goes through
    app.api.v1.auth.issue_refresh_token, which writes the row as well.
    """
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)
    to_encode.update({
        "exp": expire,
        "type": "refresh",
        "jti": jti or new_token_id(),
        "fid": family_id or new_token_id(),
    })
    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def decode_access_token(token: str) -> Optional[dict]:
    """Decode JWT access token"""
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        return payload
    except jwt.PyJWTError:
        return None