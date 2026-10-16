import uuid
from pathlib import Path
from typing import Optional

from fastapi import (APIRouter, Depends, Header, HTTPException, Query, UploadFile, File,
                     Request, status)
from fastapi.responses import Response

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.api.deps import (get_current_user, get_current_user_optional,
                          is_privileged)
from app.config import settings
from app.database import get_db
from app.models.leave import Leave
from app.models.payslip_dispute import PayslipDispute
from app.models.reimbursement import Reimbursement
from app.models.audit_log import AuditLog
from app.models.user import User
from app.utils import audit, images
from app.utils import signed_urls
from app.storage import (
    download_private_file,
    upload_private_file,
    validate_object_name,
)

router = APIRouter()

ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".pdf"}
MAX_FILE_SIZE = 5 * 1024 * 1024  # 5 MB


@router.post("", status_code=status.HTTP_201_CREATED)
async def upload_file(
    request: Request,
    file: UploadFile = File(...),
    purpose: str = Query("document", pattern="^(document|avatar)$"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Upload a document (e.g. reimbursement receipt) and get back its URL.

    `purpose` says how small the image may safely be made. An avatar is drawn
    at about forty pixels and is fetched on every page load, so it is cut down
    hard and converted to WebP; a document is only capped, because a scanned
    CNIC has small print on it that has to stay readable.
    """
    extension = Path(file.filename or "").suffix.lower()
    if extension not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"File type '{extension or 'unknown'}' not allowed. "
                   f"Allowed types: {', '.join(sorted(ALLOWED_EXTENSIONS))}",
        )

    contents = await file.read()
    if len(contents) > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="File too large. Maximum size is 5 MB.",
        )

    # Shrink before storing, never after: what is written is what is served,
    # so there is no second copy to keep in step and no resize on the hot path.
    media_type = file.content_type
    is_avatar = purpose == "avatar"
    contents, media_type, new_extension = images.shrink(
        contents, media_type,
        max_edge=images.AVATAR_MAX_EDGE if is_avatar else images.DOCUMENT_MAX_EDGE,
        to_webp=is_avatar)
    if new_extension:
        extension = new_extension

    # A random name prevents collisions and path traversal via the client filename.
    stored_name = f"{uuid.uuid4().hex}{extension}"
    await upload_private_file(stored_name, contents, media_type)

    # Also the access grant: an object is not attached to any record until the
    # caller saves the returned URL, so without this the uploader could not
    # read back the file they just sent (profile picture preview, receipt
    # confirmation) until the enclosing form was submitted.
    audit.record(
        db, "file.uploaded", actor=current_user, request=request,
        target_type="file", detail={"object": stored_name, "size": len(contents)},
    )

    return {
        "url": f"{str(request.base_url).rstrip('/')}{request.url.path}/{stored_name}",
        "filename": file.filename,
        "size": len(contents),
    }


def _owns_object(db: Session, user: User, stored_name: str) -> bool:
    """
    True when this object is attached to one of the caller's own records.

    Stored names are random UUIDs, but obscurity is not access control: every
    *_url field is handed out in API responses (UserResponse carries
    cnic_front_url for anyone the caller can read), so an authenticated user
    could otherwise fetch any colleague's CNIC scan or bank receipt without
    guessing anything. Matched with a suffix LIKE because the column stores a
    full URL while the route receives only the object name.
    """
    like = f"%/{stored_name}"

    if db.query(User).filter(
        User.id == user.id,
        or_(
            User.profile_picture_url.like(like),
            User.linkedin_welcome_picture_url.like(like),
            User.cnic_front_url.like(like),
            User.cnic_back_url.like(like),
        ),
    ).first():
        return True

    if db.query(Reimbursement).filter(
        Reimbursement.employee_id == user.id,
        Reimbursement.receipt_url.like(like),
    ).first():
        return True

    if db.query(Leave).filter(
        Leave.employee_id == user.id,
        Leave.medical_certificate_url.like(like),
    ).first():
        return True

    if db.query(AuditLog).filter(
        AuditLog.action == "file.uploaded",
        AuditLog.actor_id == user.id,
        AuditLog.detail.like(f'%"{stored_name}"%'),
    ).first():
        return True

    if db.query(PayslipDispute).filter(
        PayslipDispute.employee_id == user.id,
        PayslipDispute.attachment_url.like(like),
    ).first():
        return True

    return False


@router.get("/{stored_name}/signed-url")
async def signed_link(
    stored_name: str,
    ttl_seconds: int = 900,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    A short-lived URL for a file the caller is allowed to see.

    For anything an <img>, <embed> or a download link has to fetch without an
    Authorization header — a CNIC scan in the profile screen, a receipt on a
    claim. The ownership check happens here, once, before the link is issued;
    the link itself is then worth one object for a few minutes.
    """
    stored_name = validate_object_name(stored_name)
    if not is_privileged(current_user, db) and not _owns_object(db, current_user, stored_name):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found.")

    ttl = max(60, min(int(ttl_seconds), 3600))
    expires_at, signature = signed_urls.sign(stored_name, ttl)
    base = f"{settings.API_V1_STR}/upload/{stored_name}"
    return {"url": f"{base}?exp={expires_at}&sig={signature}",
            "expires_at": expires_at, "expires_in_seconds": ttl}


def request_etag_matches(stored_name: str, if_none_match: Optional[str]) -> bool:
    """
    True when the caller already holds this exact object.

    Belt and braces next to the long max-age: a browser that revalidates
    anyway — a hard refresh, a cache under pressure — gets 304 and no body
    rather than the whole file again.
    """
    if not if_none_match:
        return False
    tags = {tag.strip().lstrip("W/").strip('"') for tag in if_none_match.split(",")}
    return stored_name in tags or "*" in tags


@router.get("/{stored_name}")
async def download_file(
    stored_name: str,
    exp: Optional[int] = None,
    sig: Optional[str] = None,
    if_none_match: Optional[str] = Header(None, alias="If-None-Match"),
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    """
    Serve a stored file to a caller who can prove they may have it.

    Two proofs are accepted. A bearer token, checked against what the file is
    attached to — the API's normal path. Or a signature this server issued for
    this object, which is the only thing that works from an <img> tag, since a
    browser sends no Authorization header when it loads an image. Without one
    of the two, 404 rather than 403: the response should not confirm that
    somebody else's file exists.
    """
    stored_name = validate_object_name(stored_name)

    allowed = signed_urls.verify(stored_name, exp, sig)
    if not allowed and current_user is not None:
        # Admin/HR review every employee's documents as part of their job;
        # everyone else gets only what is attached to their own records.
        allowed = (is_privileged(current_user, db)
                   or _owns_object(db, current_user, stored_name))
    if not allowed:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found.")

    content, media_type = await download_private_file(stored_name)
    # A stored name is a fresh uuid4 for every upload, so the bytes behind one
    # never change: replacing a profile picture writes a new object and leaves
    # this one untouched. That makes the response immutable, and an immutable
    # object has no business being refetched.
    #
    # It was "no-store" unless the request carried a signature, so a 458KB
    # profile picture was downloaded again on every single page load. The
    # signature's expiry is not a reason to re-ask either: it governs whether
    # a browser may FETCH the object, not how long it may keep what it was
    # already allowed to have.
    #
    # private, so shared caches never hold somebody's CNIC scan; the browser
    # cache is per profile, and these responses are access-controlled above.
    headers = {
        "Cache-Control": "private, max-age=31536000, immutable",
        "ETag": f'"{stored_name}"',
    }
    if request_etag_matches(stored_name, if_none_match):
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers=headers)
    return Response(content=content, media_type=media_type, headers=headers)
