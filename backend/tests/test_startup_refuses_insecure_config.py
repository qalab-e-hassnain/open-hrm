"""
Production refuses to start when its settings leave it exposed.

This is the half that `test_unsafe_config.py` cannot cover. That file proves
the checks *notice*; this one proves the process *stops*. The distinction
matters because the previous version of this code noticed everything and
stopped at nothing: it logged a warning and served traffic anyway, which is
the same outcome as having no check, reached more expensively.

So these tests call the real startup function with real Settings objects and
assert on what it does — raises, or returns — rather than on what it logs or
what the source looks like.
"""
import logging

import pytest

from app.config import Settings
from app.main import InsecureConfiguration, _check_configuration

REAL_KEY = "n7Qw2bK9pLzR4vTfX8mYhJ3sD6gA1cEuW5oN0iVrBtH"

SAFE = dict(
    DATABASE_URL="postgresql://u:p@db.internal:5432/hrm",
    SECRET_KEY=REAL_KEY,
    ALLOWED_ORIGINS="https://hr.example.com",
    DEBUG=False,
    PORTAL_URL="https://hr.example.com",
    ENVIRONMENT="production",
    CRON_SECRET="a-cron-secret",
    SMTP_HOST="smtp.example.com",
    SMTP_FROM_EMAIL="hr@example.com",
    SUPABASE_URL="https://x.supabase.co",
    SUPABASE_SERVICE_ROLE_KEY="service-role",
)


@pytest.fixture
def startup(monkeypatch):
    """Run the real startup check against settings of our choosing."""
    import app.main

    def run(**overrides):
        monkeypatch.setattr(app.main, "settings",
                            Settings(**{**SAFE, **overrides}))
        return _check_configuration()

    return run


class TestItRefusesToServe:

    @pytest.mark.parametrize("overrides, setting", [
        ({"ALLOWED_ORIGINS": "*"}, "ALLOWED_ORIGINS"),
        ({"DEBUG": True}, "DEBUG"),
        ({"SECRET_KEY": "short"}, "SECRET_KEY"),
        ({"SECRET_KEY": "change-me-and-this-one-is-long-enough"}, "SECRET_KEY"),
        ({"PORTAL_URL": "http://hr.example.com"}, "PORTAL_URL"),
    ])
    def test_one_bad_setting_is_enough(self, startup, overrides, setting):
        with pytest.raises(InsecureConfiguration) as raised:
            startup(**overrides)
        assert setting in str(raised.value)

    def test_it_reports_all_of_them_not_just_the_first(self, startup):
        """
        Fixing one and being refused again for the next is a slow way to
        learn there were three.
        """
        with pytest.raises(InsecureConfiguration) as raised:
            startup(ALLOWED_ORIGINS="*", DEBUG=True, SECRET_KEY="short")
        message = str(raised.value)
        assert "3 insecure setting(s)" in message
        for setting in ("ALLOWED_ORIGINS", "DEBUG", "SECRET_KEY"):
            assert setting in message

    def test_the_refusal_is_printed_where_it_cannot_be_missed(
            self, startup, capsys):
        """
        The exception carries a summary; the list of problems and their fixes
        goes to stderr, because a traceback buries its own message under the
        stack and the stack is the least useful part here.
        """
        with pytest.raises(InsecureConfiguration):
            startup(ALLOWED_ORIGINS="*")
        err = capsys.readouterr().err
        assert "REFUSING TO START" in err
        assert "ALLOWED_ORIGINS" in err
        assert "hr.yourcompany.com" in err, "says what a good value looks like"
        assert "ENVIRONMENT=development" in err, "says how to opt out locally"

    def test_an_unrecognised_environment_is_treated_as_production(self, startup):
        """A typo must not be a way to switch the checks off."""
        with pytest.raises(InsecureConfiguration):
            startup(ENVIRONMENT="prodcution", ALLOWED_ORIGINS="*")


class TestItStartsWhenItShould:

    def test_a_correctly_configured_deployment_starts(self, startup):
        assert startup() is None

    def test_development_gets_a_warning_and_carries_on(self, startup, caplog):
        """
        Refusing to start on a laptop with permissive CORS would only teach
        people to delete the check. It still has to say so, though.
        """
        with caplog.at_level(logging.WARNING, logger="app.startup"):
            assert startup(ENVIRONMENT="development", ALLOWED_ORIGINS="*",
                           DEBUG=True, PORTAL_URL="http://localhost:5173") is None
        logged = caplog.text
        assert "INSECURE CONFIGURATION" in logged
        assert "ALLOWED_ORIGINS" in logged
        assert "in production it would refuse to start" in logged.lower()

    def test_advisories_do_not_stop_a_production_start(self, startup, caplog):
        """
        Scheduled jobs not running is a problem somebody notices. Being wide
        open is not. Only the second kind blocks startup.
        """
        with caplog.at_level(logging.WARNING, logger="app.startup"):
            assert startup(CRON_SECRET="", SMTP_HOST="", SMTP_FROM_EMAIL="",
                           SUPABASE_URL="", SUPABASE_SERVICE_ROLE_KEY="") is None
        assert "CRON_SECRET" in caplog.text


class TestTheCheckActuallyRunsAtImport:

    def test_importing_the_app_calls_it(self):
        """
        A check that is only ever called by its tests protects nothing. This
        pins the call site: app/main.py must invoke it at module level, so
        that `uvicorn app.main:app` cannot bypass it.
        """
        from pathlib import Path
        source = (Path(__file__).resolve().parent.parent
                  / "app" / "main.py").read_text()
        assert "\n_check_configuration()\n" in source, (
            "app/main.py no longer calls _check_configuration() at import "
            "time, so a misconfigured deployment would start normally")
