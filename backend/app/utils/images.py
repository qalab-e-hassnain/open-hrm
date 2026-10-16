"""
Shrink an uploaded image before it is stored.

A phone camera produces a several-megapixel photograph; a profile picture is
drawn at about forty pixels square. One employee's avatar was a 458KB PNG,
fetched on every page load, taking longer to arrive than every API call on
the page put together.

Nothing here is clever. It makes a large image smaller, leaves everything
else exactly as it was, and never fails an upload: if the bytes cannot be
read as an image, or the result would be no smaller, the original is stored.
A rejected upload is a worse outcome than a large one.
"""
import io
import logging
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

# Drawn at ~40px, but retina displays and the profile page's larger preview
# make a small source look soft, so there is room above what is needed.
AVATAR_MAX_EDGE = 512
# Big enough that the small print on a scanned CNIC stays readable, small
# enough that a 12-megapixel phone photo does not go to the database as-is.
DOCUMENT_MAX_EDGE = 2400

WEBP_QUALITY = 82

# Formats worth touching. A PDF is not an image, and an animated GIF would
# lose its animation, so both are left alone.
RESIZABLE = {"image/jpeg", "image/png", "image/webp"}


def shrink(content: bytes, content_type: Optional[str],
           max_edge: int = DOCUMENT_MAX_EDGE,
           to_webp: bool = False) -> Tuple[bytes, Optional[str], Optional[str]]:
    """
    Return (bytes, content_type, extension) — the original if nothing helped.

    `extension` is None when the format has not changed, so the caller keeps
    the name it already chose.
    """
    if not content or (content_type or "").lower() not in RESIZABLE:
        return content, content_type, None

    try:
        from PIL import Image, ImageOps
    except Exception:                                    # pragma: no cover
        logger.warning("Pillow is unavailable; storing the image unchanged")
        return content, content_type, None

    try:
        with Image.open(io.BytesIO(content)) as image:
            # An animated image would be flattened to its first frame, which
            # is a surprising thing to do to somebody's upload.
            if getattr(image, "n_frames", 1) > 1:
                return content, content_type, None

            # Phones record orientation in EXIF rather than in the pixels, so
            # without this a portrait photo is stored on its side.
            image = ImageOps.exif_transpose(image)

            if max(image.size) > max_edge:
                image.thumbnail((max_edge, max_edge), Image.LANCZOS)

            buffer = io.BytesIO()
            if to_webp:
                # WebP keeps transparency, so a cut-out avatar survives.
                target = image if image.mode in ("RGB", "RGBA") else image.convert("RGBA")
                target.save(buffer, format="WEBP", quality=WEBP_QUALITY, method=4)
                out_type, out_ext = "image/webp", ".webp"
            else:
                fmt = (image.format or "").upper() or "JPEG"
                if fmt == "JPEG" and image.mode not in ("RGB", "L"):
                    image = image.convert("RGB")
                image.save(buffer, format=fmt)
                out_type, out_ext = content_type, None

            shrunk = buffer.getvalue()
    except Exception:
        # A file that is not really an image, or one Pillow dislikes. Store
        # what was sent; the upload is not the place to argue about it.
        logger.info("Could not process an uploaded image; storing it unchanged",
                    exc_info=True)
        return content, content_type, None

    # Converting a small, already-efficient image can make it bigger.
    if len(shrunk) >= len(content):
        return content, content_type, None
    return shrunk, out_type, out_ext
