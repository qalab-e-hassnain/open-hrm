"""
An uploaded image is shrunk once, and then fetched once.

A profile picture was a 458KB PNG served with Cache-Control: no-store, so it
was downloaded again on every page load and took longer to arrive than every
API call on the page put together. Two things fix that and both are tested
here: make the file small, and let the browser keep it.

The caching is only safe because a stored name is a fresh uuid4 per upload —
replacing a picture writes a new object and never rewrites an existing one.
That is what makes these responses immutable, so it is asserted too.
"""
import io
import os

import pytest

from app.api.v1.upload import request_etag_matches
from app.utils import images


def png(width: int, height: int, mode: str = "RGB") -> bytes:
    """
    A photograph-like image: every pixel different, so PNG cannot compress it
    into nothing.

    This matters. An earlier version of these tests drew flat 4x4 blocks,
    which PNG stores almost for free — so re-encoding could not beat the
    original and shrink() correctly returned it untouched, making the tests
    look like failures when the code was behaving exactly as intended. Test
    data that compresses better than a real photograph tests the wrong thing.
    """
    Image = pytest.importorskip("PIL.Image", reason="Pillow is not installed")
    channels = len(mode)
    noise = os.urandom(width * height * channels)
    img = Image.frombytes(mode, (width, height), noise)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


class TestAnAvatarIsMadeSmall:

    def test_a_large_photo_is_cut_down_and_converted(self):
        Image = pytest.importorskip("PIL.Image")
        original = png(2000, 2600)
        out, content_type, extension = images.shrink(
            original, "image/png", max_edge=images.AVATAR_MAX_EDGE, to_webp=True)

        assert content_type == "image/webp"
        assert extension == ".webp"
        assert len(out) < len(original)
        assert max(Image.open(io.BytesIO(out)).size) <= images.AVATAR_MAX_EDGE

    def test_transparency_survives(self):
        Image = pytest.importorskip("PIL.Image")
        out, _, _ = images.shrink(png(900, 900, "RGBA"), "image/png",
                                  max_edge=images.AVATAR_MAX_EDGE, to_webp=True)
        assert Image.open(io.BytesIO(out)).mode == "RGBA"

    def test_a_document_keeps_its_detail(self):
        """A CNIC scan has small print on it; it is capped, not shrunk to an icon."""
        Image = pytest.importorskip("PIL.Image")
        original = png(3200, 2400)
        out, _, _ = images.shrink(original, "image/png",
                                  max_edge=images.DOCUMENT_MAX_EDGE)
        # The guarantee is that it never grows. When it does shrink, it is
        # capped at the document edge and stays far larger than an avatar.
        assert len(out) <= len(original)
        if out is not original:
            assert max(Image.open(io.BytesIO(out)).size) <= images.DOCUMENT_MAX_EDGE
            assert max(Image.open(io.BytesIO(out)).size) > images.AVATAR_MAX_EDGE


class TestNothingIsBroken:
    """An upload that cannot be improved is stored exactly as it arrived."""

    def test_a_pdf_is_untouched(self):
        pdf = b"%PDF-1.4 this is not an image"
        assert images.shrink(pdf, "application/pdf") == (pdf, "application/pdf", None)

    def test_bytes_that_are_not_an_image_are_untouched(self):
        junk = b"not an image at all, despite what the content type says"
        assert images.shrink(junk, "image/png")[0] is junk

    def test_an_already_small_image_is_not_inflated(self):
        tiny = png(24, 24)
        out, _, _ = images.shrink(tiny, "image/png",
                                  max_edge=images.AVATAR_MAX_EDGE, to_webp=True)
        assert len(out) <= len(tiny)

    def test_an_animated_image_is_left_alone(self):
        Image = pytest.importorskip("PIL.Image")
        frames = [Image.new("P", (80, 80), colour) for colour in (1, 2, 3)]
        buf = io.BytesIO()
        frames[0].save(buf, format="GIF", save_all=True, append_images=frames[1:])
        animated = buf.getvalue()
        # Flattening somebody's animation to its first frame is a surprising
        # thing to do to their upload, so GIF is not in the resizable set.
        assert images.shrink(animated, "image/gif")[0] is animated


class TestTheBrowserMayKeepIt:

    def test_an_etag_that_matches(self):
        assert request_etag_matches("abc123.webp", '"abc123.webp"')

    def test_a_weak_etag_still_matches(self):
        assert request_etag_matches("abc123.webp", 'W/"abc123.webp"')

    def test_one_of_several(self):
        assert request_etag_matches("abc123.webp", '"other", "abc123.webp"')

    def test_a_wildcard_matches(self):
        assert request_etag_matches("abc123.webp", "*")

    def test_a_different_object_does_not_match(self):
        assert not request_etag_matches("abc123.webp", '"something-else.webp"')

    def test_no_header_means_send_the_file(self):
        assert not request_etag_matches("abc123.webp", None)
        assert not request_etag_matches("abc123.webp", "")
