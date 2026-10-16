"""
Turning a template into one person's document.

Rendering happens server-side and only once, at issue. The result is frozen and
hashed, because the whole value of a signature is being able to say later
exactly what was agreed to.
"""
import hashlib
import html

import re
from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from app.config import settings

# Pakistani English convention, matching the sample contract's
# "(Pakistan Rupees Eighty Thousand only)".
_ONES = ["", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine",
         "Ten", "Eleven", "Twelve", "Thirteen", "Fourteen", "Fifteen", "Sixteen",
         "Seventeen", "Eighteen", "Nineteen"]
_TENS = ["", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy", "Eighty", "Ninety"]


def _under_thousand(n: int) -> str:
    if n < 20:
        return _ONES[n]
    if n < 100:
        return (_TENS[n // 10] + (" " + _ONES[n % 10] if n % 10 else "")).strip()
    return (_ONES[n // 100] + " Hundred"
            + (" " + _under_thousand(n % 100) if n % 100 else "")).strip()


def amount_in_words(amount: float) -> str:
    """
    "Pakistan Rupees Eighty Thousand only" — the lakh/crore system, because a
    contract governed by Pakistani law reads in the local convention.
    """
    n = int(round(amount or 0))
    if n == 0:
        return "Pakistan Rupees Zero only"
    parts = []
    for divisor, label in ((10_000_000, "Crore"), (100_000, "Lakh"), (1_000, "Thousand")):
        if n >= divisor:
            parts.append(f"{_under_thousand(n // divisor)} {label}")
            n %= divisor
    if n:
        parts.append(_under_thousand(n))
    return "Pakistan Rupees " + " ".join(parts) + " only"


def ordinal(day: int) -> str:
    """1 -> 1st, matching "made at Islamabad on this 1st day of November"."""
    if 10 <= day % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")
    return f"{day}{suffix}"


def _bullets(items) -> str:
    if not items:
        return "<p><em>To be confirmed.</em></p>"
    return "<ul>" + "".join(f"<li>{html.escape(str(i))}</li>" for i in items) + "</ul>"


_SMALL = ["zero", "one", "two", "three", "four", "five", "six",
           "seven", "eight", "nine", "ten", "eleven", "twelve"]


def _words(n: int) -> str:
    return _SMALL[n] if 0 <= n < len(_SMALL) else str(n)


def _probation_context(employee, joining, today) -> dict:
    """
    The probation wording for one employee, and the facts behind it.

    Someone who has already served the probation period does not serve it
    again because a document is being issued late. The clause says so
    explicitly instead of staying silent, because silence is what leaves a
    defined term dangling in a contract somebody may later rely on.
    """
    from app.utils.leave_policy import LeavePolicyConfig
    from app.utils.probation import current_status

    months = LeavePolicyConfig.PROBATION_MONTHS
    served = (today.year - joining.year) * 12 + today.month - joining.month
    if today.day < joining.day:
        served -= 1

    # A recorded confirmation beats the arithmetic: somebody appointed
    # directly — a CFO, a senior hire, a founder — is confirmed on day one,
    # and counting their months would put a probation clause in their
    # contract that the Company never intended.
    #
    # Only a confirmation overrides, never the absence of one. current_status()
    # answers "in_probation" for anyone nobody has reviewed, deliberately, so
    # that the reminder queue finds them — but reading that as probation here
    # would hand a two-year employee with no recorded decision a fresh
    # probation clause, which is the whole thing this exists to prevent.
    try:
        confirmed = current_status(employee) == "confirmed"
    except Exception:
        confirmed = False
    serving = (served < months) and not confirmed

    if serving:
        # Adding months by hand rather than taking a dependency for it, and
        # clamping the day so 31 January plus three months is 30 April rather
        # than an error.
        import calendar as _cal
        m = joining.month - 1 + months
        year, month = joining.year + m // 12, m % 12 + 1
        ends = joining.replace(year=year, month=month,
                               day=min(joining.day, _cal.monthrange(year, month)[1]))
        clause = (
            f"The Employee shall serve a probationary period of "
            f"{_words(months)} ({months:02d}) months from the Effective Date "
            f"(the &ldquo;Probation Period&rdquo;), ending on "
            f"{ends.strftime('%d %B %Y')}.")
    else:
        # Two different reasons not to be on probation, and the clause should
        # not claim the wrong one: somebody with the service behind them has
        # completed it, while a direct appointment was never subject to it.
        completed = served >= months
        middle = ("the Employee has completed any applicable probationary period"
                  if completed else
                  "the Employee is appointed directly and is not subject to any "
                  "probationary period")
        clause = (
            "The Parties acknowledge that the Employee&rsquo;s employment with "
            f"the Company commenced on {joining.strftime('%d %B %Y')} and that "
            f"{middle}. No Probation Period applies to this Agreement, and no "
            "provision of this Agreement referring to the Probation Period "
            "shall have effect.")

    return {
        "probation_clause": clause,
        "probation_months": str(months),
        "is_on_probation": "yes" if serving else "no",
        "service_months": str(max(0, served)),
    }


def build_context(db: Session, employee, extra: Optional[dict] = None) -> dict:
    """
    Every placeholder a template may use, for one employee.

    Values are HTML-escaped here rather than at substitution time so a name
    containing an ampersand cannot break the document — and so nothing an
    employee typed into their own profile can inject markup into a contract.
    """
    from app.models.document import JobProfile
    from app.models.user import User

    profile = None
    if employee.designation:
        profile = db.query(JobProfile).filter(
            JobProfile.designation == employee.designation
        ).first()

    manager = None
    if employee.manager_id:
        manager = db.query(User).filter(User.id == employee.manager_id).first()

    gender = getattr(employee.gender, "value", employee.gender) or ""
    female = str(gender).lower() == "female"
    today = datetime.utcnow()
    joining = employee.date_of_joining or today

    ctx = {
        # Company
        "company_name": settings.COMPANY_NAME,
        "company_ntn": settings.COMPANY_REG_NO,
        "company_address": settings.COMPANY_ADDRESS,
        "company_email": settings.COMPANY_EMAIL,
        # Agreement
        "agreement_day": ordinal(today.day),
        "agreement_month": today.strftime("%B"),
        "agreement_year": str(today.year),
        "agreement_place": "Islamabad",
        # Employee
        "employee_name": employee.full_name or "",
        "employee_cnic": employee.cnic_number or "",
        "employee_address": employee.address or "",
        "employee_id": employee.employee_id or "",
        "father_name": employee.father_name or "",
        # The sample contract reads "X d/o Y" — s/o or d/o follows gender.
        "parentage": f"{'d/o' if female else 's/o'} {employee.father_name or ''}".strip(),
        "pronoun_subject": "she" if female else "he",
        "pronoun_object": "her" if female else "him",
        "pronoun_possessive": "her" if female else "his",
        # Capitalised variants, because a pronoun opening a sentence in a
        # letter an employee shows a future employer must not read "he has
        # completed all responsibilities".
        "Pronoun_subject": "She" if female else "He",
        "Pronoun_possessive": "Her" if female else "His",
        # Role
        "designation": employee.designation or "",
        "department": employee.department or "",
        "reporting_head": (profile.reports_to_title if profile and profile.reports_to_title
                           else (manager.designation if manager else "the Reporting Head")),
        "date_of_joining": joining.strftime("%d %B %Y"),
        "effective_date": joining.strftime("%d %B %Y"),
        # Probation, decided by service rather than by the issue date.
        #
        # Most of these agreements are being issued to people who have
        # already worked here for a year or more, because the contract was
        # never issued or has lapsed. A probation clause that runs from the
        # agreement date would restart probation for someone with two years'
        # service — and the agreement withholds benefits and allows a week's
        # notice during it, so that is not a formality.
        #
        # Templates should print {{probation_clause}} rather than referring
        # to a "Probation Period" that nothing defines.
        **_probation_context(employee, joining, today),
        # Pay
        "gross_salary": f"{(employee.base_salary or 0):,.0f}",
        "gross_salary_words": amount_in_words(employee.base_salary or 0),
        # Appendices, from the job profile
        "job_summary": html.escape(profile.summary) if profile and profile.summary else "",
        "job_responsibilities": _bullets(profile.responsibilities if profile else []),
        "kpis": _bullets(profile.kpis if profile else []),
        # Signatory
        "signatory_name": "",
        "signatory_designation": "",
    }
    # Escape the plain text values; the bullet lists are already safe markup.
    raw_html_keys = {"job_responsibilities", "kpis", "job_summary",
                     "probation_clause"}
    for k, v in list(ctx.items()):
        if k not in raw_html_keys:
            ctx[k] = html.escape(str(v))
    if extra:
        ctx.update({k: html.escape(str(v)) for k, v in extra.items()})
    return ctx


# Case-sensitive on purpose: {{Pronoun_subject}} and {{pronoun_subject}}
# are different keys, which is how a sentence-opening pronoun gets a
# capital without a second template.
_PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z0-9_]+)\s*\}\}")


def render(body_html: str, context: dict) -> str:
    """
    Substitute {{placeholders}}.

    An unknown placeholder is left visible rather than blanked: a contract with
    a literal {{gross_salary}} on it is obviously wrong and gets caught, while
    one silently missing the salary line reads as complete and does not.
    """
    return _PLACEHOLDER.sub(lambda m: context.get(m.group(1), m.group(0)), body_html)


def content_hash(body_html: str) -> str:
    """SHA-256 of the exact bytes signed — the anchor for "this has not changed"."""
    return hashlib.sha256(body_html.encode("utf-8")).hexdigest()


def missing_placeholders(body_html: str, context: dict) -> list:
    """Which placeholders a template uses that this employee cannot fill."""
    used = set(_PLACEHOLDER.findall(body_html))
    return sorted(p for p in used if not context.get(p))
