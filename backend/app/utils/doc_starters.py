"""
Skeletons HR starts a new template from.

Deliberately not finished legal text. Inventing an NDA or a harassment policy
that reads as authoritative is worse than offering nothing: someone would issue
it. Each carries the document's shape — heading, parties, signature block, the
right placeholders — and a visible marker where the company's own wording goes,
so what is missing is obvious on screen rather than discovered later.
"""
from typing import Dict, List

_SIGNATURE_BLOCK = """<div class="signature-block">
  <div class="signatory">
    <p class="party">For and on behalf of the Company</p>
    <p class="entity">{{company_name}}</p>
    <p class="rule">&nbsp;</p>
    <p class="field"><span>Name</span> {{signatory_name}}</p>
    <p class="field"><span>Designation</span> {{signatory_designation}}</p>
  </div>
  <div class="signatory">
    <p class="party">By the Employee</p>
    <p class="entity">&nbsp;</p>
    <p class="rule">&nbsp;</p>
    <p class="field"><span>Name</span> {{employee_name}}</p>
    <p class="field"><span>Identification No</span> {{employee_cnic}}</p>
  </div>
</div>"""

_TODO = ('<p class="todo"><strong>[ Replace this with the company\'s own wording. ]</strong></p>')


def _doc(title: str, intro: str, sections: List[str], signed: bool = True) -> str:
    body = [f'<h1 class="doc-title">{title}</h1>', f"<p>{intro}</p>"]
    for heading in sections:
        body.append(f"<h2>{heading}</h2>")
        body.append(_TODO)
    if signed:
        body.append("<h2>ACKNOWLEDGEMENT</h2>")
        body.append("<p>By signing below, {{pronoun_subject}} confirms that "
                    "{{pronoun_subject}} has read and understood this document.</p>")
        body.append(_SIGNATURE_BLOCK)
    return "\n".join(body)


STARTERS: Dict[str, dict] = {
    "nda": {
        "kind": "nda",
        "title": "Non-Disclosure Agreement",
        "requires_signature": True,
        "note": ("A standalone NDA. Note the Key Employee Agreement already contains one as "
                 "Appendix C — this is for contractors, or staff on a different contract."),
        "body_html": _doc(
            "NON-DISCLOSURE AGREEMENT",
            "This Agreement is made at {{agreement_place}} on this {{agreement_day}} day of "
            "{{agreement_month}}, {{agreement_year}} between {{company_name}} "
            "(NTN {{company_ntn}}) and {{employee_name}} {{parentage}}, holding "
            "Identification No. {{employee_cnic}}.",
            ["DEFINITION OF CONFIDENTIAL INFORMATION", "OBLIGATIONS OF THE EMPLOYEE",
             "EXCLUSIONS", "TERM AND SURVIVAL", "REMEDIES", "GOVERNING LAW"]),
    },
    "offer_letter": {
        "kind": "offer_letter",
        "title": "Offer of Employment",
        "requires_signature": True,
        "note": "Issued before joining. Acceptance is the employee's signature.",
        "body_html": _doc(
            "OFFER OF EMPLOYMENT",
            "Dear {{employee_name}}, we are pleased to offer you the position of "
            "{{designation}} in the {{department}} department at {{company_name}}, "
            "reporting to {{reporting_head}}, at a gross monthly salary of "
            "PKR {{gross_salary}}/- ({{gross_salary_words}}), effective "
            "{{date_of_joining}}.",
            ["TERMS OF THE OFFER", "PROBATION", "DOCUMENTS REQUIRED ON JOINING",
             "VALIDITY OF THIS OFFER"]),
    },
    "handbook": {
        "kind": "handbook",
        "title": "Employee Handbook",
        "requires_signature": False,
        "note": ("Acknowledged rather than signed — set requires_signature false so the "
                 "employee confirms they have read it."),
        "body_html": _doc(
            "EMPLOYEE HANDBOOK",
            "This handbook applies to all employees of {{company_name}} and forms part of "
            "the terms of your employment.",
            ["WORKING HOURS AND ATTENDANCE", "CODE OF CONDUCT", "USE OF COMPANY PROPERTY",
             "HEALTH AND SAFETY", "DISCIPLINARY PROCEDURE"],
            signed=False),
    },
    "leave_policy": {
        "kind": "policy",
        "title": "Leave Policy",
        "requires_signature": False,
        "note": ("The system already enforces 14 annual / 15 sick / 12 personal days, 48 "
                 "hours' notice for personal leave and one team member off per day. Keep "
                 "the wording here in step with those numbers or the two will disagree."),
        "body_html": _doc(
            "LEAVE POLICY",
            "This policy sets out leave entitlement and the process for applying, for all "
            "employees of {{company_name}}. It takes effect from 01 January 2026.",
            ["ANNUAL LEAVE", "SICK LEAVE", "PERSONAL LEAVE", "BEREAVEMENT AND PARENTAL LEAVE",
             "APPLYING FOR LEAVE", "TEAM COVER"],
            signed=False),
    },
    "harassment_policy": {
        "kind": "policy",
        "title": "Anti-Harassment Policy",
        "requires_signature": True,
        "note": ("Signed rather than acknowledged: a harassment policy is usually one an "
                 "employer wants a signature against."),
        "body_html": _doc(
            "PROTECTION AGAINST HARASSMENT POLICY",
            "{{company_name}} is committed to a workplace free from harassment. This policy "
            "applies to all employees and is issued in line with the Protection Against "
            "Harassment of Women at the Workplace Act, 2010.",
            ["SCOPE AND DEFINITIONS", "PROHIBITED CONDUCT", "HOW TO RAISE A COMPLAINT",
             "THE INQUIRY COMMITTEE", "CONFIDENTIALITY AND PROTECTION FROM RETALIATION",
             "CONSEQUENCES OF A BREACH"]),
    },
    "blank": {
        "kind": "other",
        "title": "",
        "requires_signature": True,
        "note": "An empty document with the standard heading and signature block.",
        "body_html": ('<h1 class="doc-title">DOCUMENT TITLE</h1>\n' + _TODO + "\n"
                      + _SIGNATURE_BLOCK),
    },
}


# What a template author may use. Grouped because a flat list of 31 keys is not
# a picker anyone can use.
PLACEHOLDER_GROUPS = [
    ("Employee", [
        ("employee_name", "Full name"),
        ("employee_cnic", "CNIC number"),
        ("employee_address", "Home address"),
        ("employee_id", "Employee ID, e.g. QG-2024-0002"),
        ("father_name", "Father's name"),
        ("parentage", "s/o or d/o plus father's name, per gender"),
        ("date_of_joining", "Joining date, e.g. 01 May 2024"),
        ("effective_date", "Same as joining date"),
    ]),
    ("Role", [
        ("designation", "Job title"),
        ("department", "Department"),
        ("reporting_head", "Who they report to, from the job profile"),
        ("job_summary", "Role summary, from the job profile"),
        ("job_responsibilities", "Responsibilities as a bullet list"),
        ("kpis", "KPIs as a bullet list"),
    ]),
    ("Pay", [
        ("gross_salary", "Monthly salary with separators, e.g. 300,000"),
        ("gross_salary_words", "The same in words, e.g. Pakistan Rupees Three Lakh only"),
    ]),
    ("Company", [
        ("company_name", "Registered company name"),
        ("company_ntn", "National Tax Number"),
        ("company_address", "Registered office"),
        ("company_email", "Contact address"),
    ]),
    ("Signatory", [
        ("signatory_name", "Whoever issues the document"),
        ("signatory_designation", "Their job title"),
    ]),
    ("Date", [
        ("agreement_day", "Day of issue with its ordinal, e.g. 4th"),
        ("agreement_month", "Month name, e.g. September"),
        ("agreement_year", "Year, e.g. 2026"),
        ("agreement_place", "Where the document is made, e.g. Islamabad"),
    ]),
    ("Pronouns", [
        ("pronoun_subject", "he / she"),
        ("Pronoun_subject", "He / She — for the start of a sentence"),
        ("pronoun_object", "him / her"),
        ("pronoun_possessive", "his / her"),
        ("Pronoun_possessive", "His / Her — for the start of a sentence"),
    ]),
]
