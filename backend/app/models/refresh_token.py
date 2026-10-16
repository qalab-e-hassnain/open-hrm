"""
One row per refresh token ever issued, so a stolen one can be taken away.

A refresh token used to be a bare JWT: signed, valid for seven days, and
unknown to the server. That has two consequences people tend to discover
separately. The first is that signing out did nothing — the token kept
working until it expired, because nothing anywhere recorded that it should
not. The second is worse: rotating the token on every refresh looked like it
limited the damage from a theft, but the *old* token stayed valid too, so an
attacker who copied one out of a browser had a week of access regardless of
what the real user did.

Keeping a record fixes both, and enables the part that actually matters:
reuse detection. Tokens are issued in a *family* — a chain where each refresh
supersedes the last. A correctly behaving client only ever presents the newest
link. So a request presenting a link that has already been superseded means
two parties hold tokens from the same chain, which has exactly one innocent
explanation (a client that retried after a response it never received) and one
guilty one. Both are handled the same way, because the safe response to
"somebody may have a copy of this session" is to end the session: the whole
family is revoked at once, the attacker's foothold dies with it, and the real
user is signed out and has to sign in again — which is how they find out.

What is stored is the token's id (`jti`), not the token. The JWT's signature
already proves the bearer is presenting something this deployment issued; the
row only has to answer "is it still the current one". Storing the token itself
would turn this table into a set of live credentials for no benefit.
"""
from datetime import datetime

from sqlalchemy import (Column, DateTime, ForeignKey, Index, Integer, String)
from sqlalchemy.orm import relationship

from app.database import Base


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"

    id = Column(Integer, primary_key=True, index=True)

    # The `jti` claim of the token this row describes. Unique because a
    # repeated id would make "which token is this?" ambiguous at exactly the
    # moment the answer decides whether to revoke somebody's session.
    jti = Column(String(64), unique=True, nullable=False, index=True)

    # The chain this token belongs to. Every refresh issues a new jti under
    # the same family_id, and revocation operates on the family.
    family_id = Column(String(64), nullable=False, index=True)

    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)

    issued_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    expires_at = Column(DateTime, nullable=False)

    # When the family began. Carried forward unchanged through every rotation
    # so that refreshing cannot extend a session indefinitely: a chain that
    # started more than REFRESH_TOKEN_FAMILY_MAX_DAYS ago stops refreshing
    # however recently its newest link was issued. Without this, a seven-day
    # token refreshed every six days is a permanent one.
    family_started_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    # Set the moment this token stops being usable, for any reason.
    revoked_at = Column(DateTime, nullable=True)
    # "rotated", "signed_out", "reuse_detected", "user_deactivated",
    # "password_reset", "all_sessions_revoked"
    revoked_reason = Column(String(40), nullable=True)
    # The jti that replaced this one. Lets the chain be walked when working
    # out what happened after a reuse.
    replaced_by = Column(String(64), nullable=True)

    # Recorded to make a reuse report legible — "this family was used from two
    # different places" is a far more useful incident note than a bare count.
    # Truncated rather than validated; it is attacker-supplied text.
    user_agent = Column(String(256), nullable=True)
    ip_address = Column(String(64), nullable=True)

    user = relationship("User", lazy="joined")

    __table_args__ = (
        # The two lookups this table exists for: "is this jti still live"
        # (covered by the unique index on jti) and "revoke this family".
        Index("ix_refresh_tokens_family_active", "family_id", "revoked_at"),
        # Pruning expired rows on the cron sweep.
        Index("ix_refresh_tokens_expires_at", "expires_at"),
    )

    @property
    def is_live(self) -> bool:
        return self.revoked_at is None and self.expires_at > datetime.utcnow()

    def __repr__(self) -> str:
        state = "live" if self.is_live else (self.revoked_reason or "expired")
        return f"<RefreshToken {self.jti[:8]}… user={self.user_id} {state}>"
