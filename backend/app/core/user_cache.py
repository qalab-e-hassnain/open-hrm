"""
A very short-lived, per-process cache of the signed-in user.

Why this exists
---------------
Every authenticated request looks the caller up by email to find their role
and whether they are still active. That is one indexed query, and against a
local database it would not be worth a line of code. This database is a long
way from the API — one round trip is about 170ms — so that single lookup was
costing more than most endpoints spend doing their actual work, on every
request the portal makes.

The cache holds the user's own columns, not an ORM object: an object belongs
to the session that loaded it and must never be shared between requests. The
columns are merged into the asking session with ``load=False``, which is
SQLAlchemy's documented way of bringing an object in from an external cache
without issuing a SELECT.

What it costs
-------------
A change to someone's role, their HR flag or their active state can take up
to TTL seconds to be seen by a request that is already in flight elsewhere.
Writers call ``forget()`` so the process that made the change sees it at
once, but the API runs more than one worker and a cache is per process, so
the other worker still waits out the TTL. That is why the TTL is seconds
rather than minutes.

Nothing here weakens the token check. An expired, forged or wrong-type token
is rejected before the cache is consulted, exactly as before — this only
avoids re-reading a row that was read moments ago.
"""
import time
from typing import Optional

from sqlalchemy import inspect as sa_inspect
from sqlalchemy.orm import Session, make_transient_to_detached
from sqlalchemy.orm.util import identity_key

from app.models.user import User

# Seconds a looked-up user may be reused for. Short on purpose: see above.
TTL_SECONDS = 30.0

_cache: dict = {}


def _columns(user: User) -> dict:
    """The user's own column values — no relationships, nothing lazy."""
    return {attr.key: getattr(user, attr.key)
            for attr in sa_inspect(User).mapper.column_attrs}


def get(db: Session, email: str) -> Optional[User]:
    """
    The cached user, attached to this session, or None to go and read them.

    Returns None rather than raising on anything unexpected, so a cache that
    cannot do its job falls back to the query it was avoiding.
    """
    entry = _cache.get(email)
    if entry is None:
        return None
    stored_at, columns = entry
    if (time.monotonic() - stored_at) > TTL_SECONDS:
        _cache.pop(email, None)
        return None

    # Already loaded in this session? Then that object is the right one to
    # hand back — merging over the top of it would overwrite whatever the
    # request has already done to it. The auth dependency runs against a
    # fresh session, so this is a guard rather than the usual path.
    existing = db.identity_map.get(identity_key(User, columns["id"]))
    if existing is not None:
        return existing

    try:
        detached = User(**columns)
        make_transient_to_detached(detached)
        return db.merge(detached, load=False)
    except Exception:
        _cache.pop(email, None)
        return None


def put(user: User) -> None:
    """Remember this user's columns for the next few seconds."""
    try:
        _cache[user.email] = (time.monotonic(), _columns(user))
    except Exception:
        # A cache that cannot store is not an error worth failing a request
        # for; the next request simply reads the row again.
        pass


def forget(email: Optional[str] = None) -> None:
    """
    Drop one user, or everybody.

    Call after changing anything the request path reads off the user —
    their role, HR flag, active state or email.
    """
    if email is None:
        _cache.clear()
    else:
        _cache.pop(email, None)


# --------------------------------------------------------------------------
# Eviction, driven by the mapper rather than by remembering to call forget()
# --------------------------------------------------------------------------
#
# Hooking the model means every path that changes a user evicts them — an
# endpoint, a script, a migration helper, a path written next year. Relying on
# each writer to remember would be one missed call away from somebody keeping
# a role they no longer have.
#
# These fire during flush, so a transaction that later rolls back will have
# evicted for nothing. That costs one re-read and is the safe way round.

def _forget_target(_mapper, _connection, target) -> None:
    from sqlalchemy import inspect as _inspect
    emails = {getattr(target, "email", None)}
    try:
        # The email itself may be what changed; drop the old one too.
        history = _inspect(target).attrs.email.history
        emails.update(history.deleted or ())
    except Exception:
        pass
    for address in emails:
        if address:
            forget(address)


def _install() -> None:
    from sqlalchemy import event
    event.listen(User, "after_update", _forget_target, propagate=True)
    event.listen(User, "after_delete", _forget_target, propagate=True)


_install()
