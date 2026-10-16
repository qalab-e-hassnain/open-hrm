"""
What a token must and must not be accepted for.

These exist because the JWT library was swapped: python-jose is effectively
unmaintained and carried an advisory with no fix, and it pulled in `ecdsa`,
whose maintainers have said the Minerva timing attack will not be fixed. The
replacement is PyJWT.

Swapping the library underneath every authenticated request is exactly the
kind of change that keeps working for the happy path and quietly stops
rejecting things. These check the rejections.
"""
from datetime import datetime, timedelta

import jwt
import pytest

from app.config import settings
from app.core.security import (create_access_token, create_refresh_token,
                               decode_access_token)


class TestARoundTrip:

    def test_what_goes_in_comes_back(self):
        token = create_access_token({"sub": "someone@example.com"})
        assert decode_access_token(token)["sub"] == "someone@example.com"

    def test_an_access_token_says_it_is_one(self):
        assert decode_access_token(create_access_token({"sub": "a@b.com"}))["type"] == "access"

    def test_a_refresh_token_says_it_is_one(self):
        """
        The two are not interchangeable: the auth dependency refuses anything
        whose type is not "access", so a refresh token must not pass as one.
        """
        assert decode_access_token(create_refresh_token({"sub": "a@b.com"}))["type"] == "refresh"


class TestWhatMustBeRefused:

    @pytest.mark.parametrize("rubbish", [
        "", "not-a-token", "a.b.c", "eyJhbGciOiJIUzI1NiJ9.e30.",
    ])
    def test_nonsense(self, rubbish):
        assert decode_access_token(rubbish) is None

    def test_a_tampered_signature(self):
        token = create_access_token({"sub": "a@b.com"})
        assert decode_access_token(token[:-4] + "AAAA") is None

    def test_a_tampered_payload(self):
        """Changing the subject invalidates the signature over it."""
        token = create_access_token({"sub": "employee@example.com"})
        header, payload, signature = token.split(".")
        forged = jwt.encode({"sub": "admin@example.com", "type": "access",
                             "exp": datetime.utcnow() + timedelta(hours=1)},
                            "the-wrong-secret", algorithm="HS256")
        # Same shape, signed with a key we do not hold.
        assert decode_access_token(forged) is None

    def test_an_expired_token(self):
        expired = jwt.encode(
            {"sub": "a@b.com", "type": "access",
             "exp": datetime.utcnow() - timedelta(seconds=1)},
            settings.SECRET_KEY, algorithm=settings.ALGORITHM)
        assert decode_access_token(expired) is None

    def test_one_signed_with_another_deployments_secret(self):
        """Two deployments must not accept each other's tokens."""
        theirs = jwt.encode(
            {"sub": "a@b.com", "type": "access",
             "exp": datetime.utcnow() + timedelta(hours=1)},
            "a-different-deployments-secret-key", algorithm="HS256")
        assert decode_access_token(theirs) is None


class TestTheAlgorithmIsPinned:
    """
    The advisory against the old library was only reachable "when algorithms
    are not explicitly restricted". They must stay restricted.
    """

    def test_an_unsigned_token_is_refused(self):
        """
        alg=none is the oldest JWT attack there is: strip the signature and
        claim the algorithm was never meant to be checked.
        """
        unsigned = jwt.encode(
            {"sub": "admin@example.com", "type": "access",
             "exp": datetime.utcnow() + timedelta(hours=1)},
            key="", algorithm="none")
        assert decode_access_token(unsigned) is None

    def test_decoding_names_the_algorithm(self):
        import inspect
        from app.core import security
        source = inspect.getsource(security.decode_access_token)
        assert "algorithms=" in source, (
            "decode must pin the algorithms it will accept")
