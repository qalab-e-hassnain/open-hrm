"""
An email that tells you something happened says where to go and look.

Every template announced an event — your account is ready, your leave was
approved, your payslip is out — and not one of them carried the portal
address. A new joiner was sent a password with nothing to type it into.

The welcome email gets a button, because signing in is the whole point of it.
Everything else gets the address in the footer, which is enough.
"""
import pytest

from app.config import settings
from app.utils import email as E


def templates():
    return {
        "welcome": E._welcome_email_html("Nadia New", "nadia@example.com", "pw123456"),
        "leave": E._leave_email_html("Annual", "01 Nov 2026", "02 Nov 2026", "approved", None),
        "reimbursement": E._reimbursement_email_html("approved", 5000.0),
    }


class TestEveryEmailSaysWhereToGo:

    @pytest.mark.parametrize("name", ["welcome", "leave", "reimbursement"])
    def test_the_portal_address_is_present(self, name):
        assert settings.PORTAL_URL in templates()[name]

    def test_it_is_a_real_link_not_just_text(self):
        assert f'href="{settings.PORTAL_URL}"' in templates()["welcome"]


class TestTheWelcomeEmail:

    def test_it_has_a_sign_in_button(self):
        html = templates()["welcome"]
        assert "Sign in to the portal" in html

    def test_the_button_is_a_table_so_outlook_renders_it(self):
        """
        Outlook on Windows renders through Word, which drops padding and
        background on an anchor — a styled <a> arrives as a bare blue link.
        """
        html = templates()["welcome"]
        start = html.index("Sign in to the portal")
        # The anchor sits inside a table cell carrying the background colour.
        before = html[:start]
        assert before.rindex("<table") > before.rindex("</table>") if "</table>" in before else True
        assert "background-color" in before[before.rindex("<table"):]

    def test_the_address_is_also_in_plain_text(self):
        """Some clients show only the plain part, and a button is invisible there."""
        plain = E._welcome_email_plain("Nadia New", "nadia@example.com", "pw123456")
        assert settings.PORTAL_URL in plain

    def test_the_credentials_are_still_there(self):
        html = templates()["welcome"]
        assert "nadia@example.com" in html and "pw123456" in html


class TestOnlyTheWelcomeEmailPushesSignIn:
    """A leave approval does not need a call to action; it needs a footer."""

    @pytest.mark.parametrize("name", ["leave", "reimbursement"])
    def test_no_sign_in_button_elsewhere(self, name):
        assert "Sign in to the portal" not in templates()[name]
