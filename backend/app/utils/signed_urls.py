"""
Short-lived signatures so a browser can fetch a private file in an <img> tag.

The file endpoint is authenticated, and it should be — the same bucket holds
CNIC scans and expense receipts. But an <img> tag sends no Authorization
header, so every profile picture in the portal came back 401 and rendered as a
broken image.

The fix is the one object stores have used for years: the server hands out a
URL that carries its own proof, good for a few minutes. No token in the query
string — a bearer token in a URL ends up in browser history, referrer headers
and server logs, and lasts as long as the session. A signature here is worth
exactly one object for exactly a few minutes.
"""
import hashlib
import hmac
import time
from typing import Optional, Tuple
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from app.config import settings

# For anything sensitive: long enough to load a page and re-render it a few
# times, short enough that a link copied out of the network tab is useless by
# the time it is pasted.
DEFAULT_TTL_SECONDS = 15 * 60

# Pictures get an hour. They are the least sensitive thing in the bucket, and a
# quarter of an hour means a directory page left open over lunch comes back
# covered in broken images.
PICTURE_TTL_SECONDS = 60 * 60


def _digest(object_name: str, expires_at: int) -> str:
    return hmac.new(
        settings.SECRET_KEY.encode(),
        f"{object_name}:{expires_at}".encode(),
        hashlib.sha256,
    ).hexdigest()[:32]


def sign(object_name: str, ttl_seconds: int = DEFAULT_TTL_SECONDS) -> Tuple[int, str]:
    expires_at = int(time.time()) + ttl_seconds
    return expires_at, _digest(object_name, expires_at)


def verify(object_name: str, expires_at: Optional[int], signature: Optional[str]) -> bool:
    """Constant-time check. False for anything missing, malformed or expired."""
    if not signature or expires_at is None:
        return False
    try:
        expires_at = int(expires_at)
    except (TypeError, ValueError):
        return False
    if expires_at < int(time.time()):
        return False
    return hmac.compare_digest(_digest(object_name, expires_at), signature)


def object_name_from(url: Optional[str]) -> Optional[str]:
    """The stored file name out of a full URL. None for anything unrecognisable."""
    if not url:
        return None
    name = urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1]
    return name or None


def strip_signature(url: Optional[str]) -> Optional[str]:
    """
    Remove a signature this app issued, leaving the bare file URL.

    Signatures last an hour and are minted per response; one that reaches a
    stored column is already most of the way to being dead. Call this before
    saving any URL that came back from a client, because the client is handing
    back whatever it was given — including the signature.
    """
    if not url:
        return url
    parts = urlsplit(url)
    if not parts.query:
        return url
    kept = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
            if k not in ("exp", "sig")]
    if len(kept) == len(parse_qsl(parts.query, keep_blank_values=True)):
        return url            # nothing of ours in there; not our URL to touch
    return urlunsplit(parts._replace(query=urlencode(kept)))


def sign_url(url: Optional[str], ttl_seconds: int = PICTURE_TTL_SECONDS) -> Optional[str]:
    """
    Append a fresh signature to a stored file URL.

    A URL that already carries one of OUR signatures is re-signed, not passed
    through. Passing it through is what broke three profile pictures: a client
    read a signed URL, saved it back into the record, and from then on every
    response served that same hour-old signature until it expired — after
    which the picture was permanently broken, because nothing ever refreshed
    it. A stored signature is always stale or about to be.

    A URL carrying somebody else's query string is still left alone.
    """
    if not url:
        return url
    url = strip_signature(url)
    parts = urlsplit(url)
    if parts.query:
        return url            # a foreign URL, not ours to mangle
    name = object_name_from(url)
    if not name:
        return url
    expires_at, signature = sign(name, ttl_seconds)
    return f"{url}?{urlencode({'exp': expires_at, 'sig': signature})}"
