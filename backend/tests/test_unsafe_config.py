"""
Settings that are fine on a laptop and dangerous on the internet.

Every one of these fails silently. The system works perfectly while being
wide open, so nobody finds out until somebody else does — which is why it is
reported at startup rather than left to a line in a deployment guide that the
person deploying has usually not read.
"""
import pytest

from app.config import Settings

SAFE = dict(
    DATABASE_URL="postgresql://u:p@db.internal:5432/hrm",
    SECRET_KEY="x" * 48,
    ALLOWED_ORIGINS="https://hr.example.com",
    DEBUG=False,
    PORTAL_URL="https://hr.example.com",
)


def settings(**overrides):
    return Settings(**{**SAFE, **overrides})


def test_a_careful_deployment_reports_nothing():
    assert settings().unsafe_for_production() == []


class TestWhatItCatches:

    def test_cors_open_to_every_origin(self):
        problems = settings(ALLOWED_ORIGINS="*").unsafe_for_production()
        assert any("ALLOWED_ORIGINS" in p for p in problems)

    def test_debug_left_on(self):
        problems = settings(DEBUG=True).unsafe_for_production()
        assert any("DEBUG" in p for p in problems)

    @pytest.mark.parametrize("key", ["", "short", "x" * 31])
    def test_a_guessable_secret(self, key):
        problems = settings(SECRET_KEY=key).unsafe_for_production()
        assert any("SECRET_KEY" in p for p in problems)

    def test_a_portal_served_over_plain_http(self):
        problems = settings(PORTAL_URL="http://hr.example.com").unsafe_for_production()
        assert any("http" in p for p in problems)

    def test_localhost_over_http_is_not_a_problem(self):
        """Development is not a misconfiguration."""
        problems = settings(PORTAL_URL="http://localhost:5173").unsafe_for_production()
        assert not any("PORTAL_URL" in p for p in problems)


class TestItSaysWhatToDo:
    """A warning nobody can act on gets ignored twice."""

    def test_every_problem_names_the_setting_and_a_fix(self):
        problems = settings(ALLOWED_ORIGINS="*", DEBUG=True,
                            SECRET_KEY="short").unsafe_for_production()
        assert len(problems) == 3
        for p in problems:
            assert len(p) > 40, f"too terse to act on: {p!r}"


class TestOriginParsing:

    def test_a_list_is_split_and_trimmed(self):
        s = settings(ALLOWED_ORIGINS="https://a.example.com, https://b.example.com")
        assert s.cors_origins == ["https://a.example.com", "https://b.example.com"]

    def test_a_trailing_comma_does_not_allow_an_empty_origin(self):
        s = settings(ALLOWED_ORIGINS="https://a.example.com,")
        assert s.cors_origins == ["https://a.example.com"]
