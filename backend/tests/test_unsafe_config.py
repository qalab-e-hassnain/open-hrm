"""
Settings that are fine on a laptop and dangerous on the internet.

Every one of these fails silently. The system works perfectly while being
wide open, so nobody finds out until somebody else does — which is why it is
reported at startup rather than left to a line in a deployment guide that the
person deploying has usually not read.
"""
import pytest

from app.config import Settings, looks_like_a_placeholder

# A key that looks like one `secrets.token_urlsafe` produced, because that is
# what a real deployment has. "x" * 48 was long enough for the length check
# and would now be rejected by the placeholder check for containing "xxxx" —
# correctly, since a key made of one repeated character is not a secret.
REAL_KEY = "n7Qw2bK9pLzR4vTfX8mYhJ3sD6gA1cEuW5oN0iVrBtH"

SAFE = dict(
    DATABASE_URL="postgresql://u:p@db.internal:5432/hrm",
    SECRET_KEY=REAL_KEY,
    ALLOWED_ORIGINS="https://hr.example.com",
    DEBUG=False,
    PORTAL_URL="https://hr.example.com",
    ENVIRONMENT="production",
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


class TestAPublishedSecretKey:
    """
    The worst of these, and the easiest to end up with.

    A short key has to be guessed. A key copied from an example file does not
    — it is already in the attacker's hands, in this repository, and it signs
    tokens for any account including an administrator. Length checks alone
    pass it happily.
    """

    @pytest.mark.parametrize("key", [
        "change-me-to-something-random-please",
        "CHANGEME-CHANGEME-CHANGEME-CHANGEME-1",
        "replace-this-with-a-real-secret-key-x",
        "your-secret-key-goes-right-here-ok-yes",
        "example-secret-key-for-the-docs-only-1",
        "placeholder-secret-key-not-for-real-us",
        "supersecretsupersecretsupersecretsuper",
        "testing-secret-not-a-real-one-padded-o",
        "insecure-development-key-do-not-deploy",
        "x" * 48,
    ])
    def test_a_copied_example_key_is_rejected_however_long(self, key):
        assert len(key) >= 32, "the point is that length alone does not save it"
        problems = settings(SECRET_KEY=key).unsafe_for_production()
        assert any("SECRET_KEY" in p for p in problems), key

    def test_it_says_the_key_is_public_not_merely_weak(self):
        problems = settings(SECRET_KEY="change-me-but-this-one-is-long-enough")\
            .unsafe_for_production()
        message = next(p for p in problems if "SECRET_KEY" in p)
        assert "public" in message

    def test_a_real_random_key_passes(self):
        assert not any("SECRET_KEY" in p
                       for p in settings(SECRET_KEY=REAL_KEY).unsafe_for_production())

    def test_the_repositorys_own_example_file_ships_no_key_at_all(self):
        """
        Better to refuse to start for a missing key than to run on a shared
        one. If .env.example ever gains a real-looking default, this fails.
        """
        from pathlib import Path
        example = Path(__file__).resolve().parent.parent / ".env.example"
        line = next(l for l in example.read_text().splitlines()
                    if l.startswith("SECRET_KEY="))
        value = line.split("=", 1)[1].strip()
        assert value == "" or looks_like_a_placeholder(value), (
            f".env.example ships SECRET_KEY={value!r}, which every deployment "
            "that copies it would then share")


class TestWhichEnvironmentThisIs:
    """
    Decided the strict way round: anything not recognisably development is
    production. A typo must not be a way to switch the checks off.
    """

    @pytest.mark.parametrize("value", [
        "development", "Development", "DEVELOPMENT", " dev ", "local",
        "test", "testing",
    ])
    def test_development_spellings(self, value):
        assert settings(ENVIRONMENT=value).is_production is False

    @pytest.mark.parametrize("value", [
        "production", "prod", "staging", "", "  ", "prodcution",
        "develpoment", "live", "anything-else",
    ])
    def test_everything_else_counts_as_production(self, value):
        assert settings(ENVIRONMENT=value).is_production is True

    def test_the_default_is_production(self, monkeypatch):
        """
        An omitted setting has to land on the safe side. A deployment that
        never heard of ENVIRONMENT gets the checks.

        The environment variable has to be cleared, not just the env file:
        conftest sets ENVIRONMENT=test for the whole suite so that importing
        the app does not trip the startup refusal.
        """
        monkeypatch.delenv("ENVIRONMENT", raising=False)
        monkeypatch.delenv("DEBUG", raising=False)
        s = Settings(DATABASE_URL="sqlite://", SECRET_KEY=REAL_KEY,
                     _env_file=None)
        assert s.ENVIRONMENT == "production"
        assert s.is_production is True

    def test_debug_defaults_to_off(self, monkeypatch):
        monkeypatch.delenv("DEBUG", raising=False)
        s = Settings(DATABASE_URL="sqlite://", SECRET_KEY=REAL_KEY,
                     _env_file=None)
        assert s.DEBUG is False


class TestAdvisories:
    """
    Separate from the fatal list, and the line between them is whether the
    setting leaves the system exposed or merely diminished.
    """

    def test_a_missing_cron_secret_is_advisory_not_fatal(self):
        s = settings(CRON_SECRET="")
        assert any("CRON_SECRET" in n for n in s.production_advisories())
        assert not any("CRON_SECRET" in p for p in s.unsafe_for_production())

    def test_no_mail_provider_is_advisory(self):
        s = settings(SMTP_HOST="", SMTP_FROM_EMAIL="")
        assert any("email" in n for n in s.production_advisories())

    def test_no_object_storage_is_advisory(self):
        s = settings(SUPABASE_URL="", SUPABASE_SERVICE_ROLE_KEY="")
        assert any("uploads" in n for n in s.production_advisories())

    def test_a_complete_deployment_has_nothing_to_say(self):
        s = settings(CRON_SECRET="a-cron-secret", SMTP_HOST="smtp.example.com",
                     SMTP_FROM_EMAIL="hr@example.com",
                     SUPABASE_URL="https://x.supabase.co",
                     SUPABASE_SERVICE_ROLE_KEY="service-role")
        assert s.production_advisories() == []
