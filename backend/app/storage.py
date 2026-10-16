"""Private Supabase Storage access for Vercel-hosted uploads."""

from pathlib import Path

import httpx
from fastapi import HTTPException, status

from app.config import settings


def _headers(content_type: str | None = None) -> dict[str, str]:
    if not settings.SUPABASE_URL or not settings.SUPABASE_SERVICE_ROLE_KEY:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="File storage is not configured.",
        )

    headers = {
        "Authorization": f"Bearer {settings.SUPABASE_SERVICE_ROLE_KEY}",
        "apikey": settings.SUPABASE_SERVICE_ROLE_KEY,
    }
    if content_type:
        headers["Content-Type"] = content_type
    return headers


def _object_url(object_name: str) -> str:
    return (
        f"{settings.SUPABASE_URL.rstrip('/')}/storage/v1/object/"
        f"{settings.SUPABASE_STORAGE_BUCKET}/{object_name}"
    )


async def upload_private_file(
    object_name: str, content: bytes, content_type: str | None,
) -> None:
    """Upload an object to the private HRM bucket without exposing its key."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            _object_url(object_name),
            content=content,
            headers={**_headers(content_type), "x-upsert": "false"},
        )
    if response.is_error:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Could not store the uploaded file.",
        )


async def download_private_file(object_name: str) -> tuple[bytes, str]:
    """Download an object after the API has authenticated the requester."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(_object_url(object_name), headers=_headers())
    if response.status_code == status.HTTP_404_NOT_FOUND:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found.")
    if response.is_error:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Could not retrieve the uploaded file.",
        )
    return response.content, response.headers.get("content-type", "application/octet-stream")


def validate_object_name(object_name: str) -> str:
    """Allow only the UUID-style file names produced by this API."""
    if Path(object_name).name != object_name or Path(object_name).suffix.lower() not in {
        ".jpg", ".jpeg", ".png", ".gif", ".webp", ".pdf",
    }:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found.")
    return object_name
