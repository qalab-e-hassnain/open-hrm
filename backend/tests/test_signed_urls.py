"""
A signature belongs on the way out, never in the column.

Signatures last an hour and are minted per response. One that reaches storage
is already most of the way to being dead, and the picture it points at breaks
permanently once it expires — which is what happened to four profile pictures
before any of this existed.
"""
import pytest

from app.utils.signed_urls import PICTURE_TTL_SECONDS, sign_url, strip_signature, verify
from app.utils.signed_urls import object_name_from

BASE = "https://server.example.com/api/v1/upload/abc123.png"
STALE = BASE + "?exp=1791271300&sig=4ce81f4e099a611a4fc697ef467b5795"


def _parts(url):
    from urllib.parse import parse_qs, urlsplit
    q = parse_qs(urlsplit(url).query)
    return int(q["exp"][0]), q["sig"][0]


class TestStripSignature:
    def test_removes_our_own_signature(self):
        assert strip_signature(STALE) == BASE

    def test_leaves_a_clean_url_alone(self):
        assert strip_signature(BASE) == BASE

    def test_leaves_somebody_elses_query_string_alone(self):
        foreign = "https://cdn.example.com/a.png?v=3&w=200"
        assert strip_signature(foreign) == foreign

    def test_handles_none_and_empty(self):
        assert strip_signature(None) is None
        assert strip_signature("") == ""


class TestSignUrl:
    def test_signs_a_clean_url(self):
        signed = sign_url(BASE)
        exp, sig = _parts(signed)
        assert verify(object_name_from(signed), exp, sig)

    def test_re_signs_a_stale_signature_rather_than_passing_it_through(self):
        # The bug: a stored signature was returned untouched, so the picture
        # was permanently broken an hour after it was saved.
        signed = sign_url(STALE)
        exp, sig = _parts(signed)
        assert sig != "4ce81f4e099a611a4fc697ef467b5795"
        assert verify(object_name_from(signed), exp, sig)

    def test_the_fresh_signature_actually_validates(self):
        exp, sig = _parts(sign_url(STALE))
        assert verify("abc123.png", exp, sig) is True

    def test_a_foreign_url_is_never_mangled(self):
        foreign = "https://cdn.example.com/a.png?v=3"
        assert sign_url(foreign) == foreign

    def test_none_stays_none(self):
        assert sign_url(None) is None


class TestTheColumnNeverHoldsASignature:
    def test_the_model_strips_one_on_write(self):
        from app.models.user import _strip_stored_signatures

        class _U:
            profile_picture_url = STALE
            linkedin_welcome_picture_url = None
            cnic_front_url = BASE
            cnic_back_url = "https://cdn.example.com/x.png?v=1"

        u = _U()
        _strip_stored_signatures(None, None, u)
        assert u.profile_picture_url == BASE          # ours, stripped
        assert u.cnic_front_url == BASE               # already clean
        assert u.cnic_back_url == "https://cdn.example.com/x.png?v=1"   # foreign
        assert u.linkedin_welcome_picture_url is None
