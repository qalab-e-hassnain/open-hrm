"""
Role-specific "how to use the HRM" email sent when the portal goes live.

Four audiences rather than three, because a plain manager and an HR-flagged
manager have materially different reach: leave approval is require_admin_or_hr,
while reimbursement approval is require_manager. Telling a regular manager to
approve leave would send them hunting for a control they do not have.
"""

from app.config import settings
from app.utils.email import _email_shell, send_email

# Both from settings: this system is run by other companies for their own
# staff, and a joiner must be pointed at their portal and their HR team.
PORTAL_URL = settings.PORTAL_URL
SUPPORT_EMAIL = settings.HR_REPLY_TO or settings.COMPANY_EMAIL

COMPANY_NAME = settings.COMPANY_NAME
BRAND_RED = "#C8102E"
BRAND_RED_SOFT = "#FDF2F4"
BORDER_LIGHT = "#E5E7EB"
TEXT_HEADING = "#111827"
TEXT_BODY = "#374151"
TEXT_MUTED = "#6B7280"


def _section(title: str, items: list) -> str:
    lis = "".join(
        f'<li style="margin:0 0 10px 0;color:{TEXT_BODY};font-size:15px;line-height:1.6;">{i}</li>'
        for i in items
    )
    return (
        f'<p style="margin:26px 0 10px 0;color:{TEXT_HEADING};font-size:15px;font-weight:700;">{title}</p>'
        f'<ul style="margin:0;padding-left:20px;">{lis}</ul>'
    )


def _callout(text: str) -> str:
    return (
        f'<table role="presentation" width="100%" cellspacing="0" cellpadding="0" '
        f'style="background-color:{BRAND_RED_SOFT};border-radius:8px;border:1px solid {BORDER_LIGHT};margin:24px 0;">'
        f'<tr><td style="padding:16px 20px;border-left:3px solid {BRAND_RED};">'
        f'<p style="margin:0;color:{TEXT_BODY};font-size:14px;line-height:1.6;">{text}</p>'
        f"</td></tr></table>"
    )


# Everyone gets these, in this order, regardless of role.
def _common_blocks() -> str:
    return (
        _section(
            "1. Sign in and finish your profile",
            [
                f'Go to <a href="{PORTAL_URL}" style="color:{BRAND_RED};">{PORTAL_URL}</a> and sign in with the '
                "email and password from your welcome email.",
                "Tick <strong>Remember this device</strong> — without it your session expires after 15 minutes "
                "and you will be signed out repeatedly.",
                "Open <strong>My Profile</strong> and complete every field: CNIC number and scans, father's name, "
                "date of birth, marital status, address, emergency contact, and your bank details "
                "(account title, account number, bank and branch code).",
                "Your bank details are what payroll pays into, so an incomplete profile can delay your salary.",
            ],
        )
        + _section(
            "2. Daily check-in and check-out",
            [
                "Check in from the <strong>Attendance</strong> page when you arrive. You must be physically at the "
                f"office — check-in is refused beyond {settings.OFFICE_RADIUS_METERS:g} metres and will tell you how far away you are.",
                "Check out when you leave. Unlike check-in, you can check out from anywhere.",
                f"A session closes automatically once it reaches {settings.STANDARD_WORK_HOURS_PER_DAY:g} hours. "
                "To record overtime beyond that, check in again — the second session is counted as overtime.",
                "A day can hold several sessions. Your daily total is the sum of them, so a lunch break in the "
                "middle is normal and nothing is lost by checking out and back in.",
            ],
        )
        + _section(
            "3. Applying for leave",
            [
                "Apply under <strong>Leave</strong>: pick the type, the dates and a reason.",
                "Company policy asks for <strong>14 days notice</strong> on planned leave. You can still submit with "
                "less, but the request is flagged for review.",
                "Sick leave over a few days may require a medical certificate before it can be approved — upload it "
                "with the request.",
                "You cannot book more than your remaining allowance. Requests still awaiting a decision are held "
                "against your balance too, so several pending requests cannot quietly overdraw it.",
            ],
        )
        + _section(
            "4. Check your leave balance — and tell us if it looks wrong",
            [
                "Your <strong>Leave Balance</strong> page shows this year's allowance and what has been used: "
                "24 annual, 15 sick, 12 personal, 5 bereavement.",
                "<strong>Your leave taken earlier in 2026 has been loaded into the system from the old "
                "spreadsheet.</strong> Please check it against your own record now.",
                "If a leave is missing, duplicated, has the wrong dates, or is filed under the wrong type, reply to "
                "this email. It is far easier to correct before payroll starts reading these numbers.",
            ],
        )
        + _section(
            "5. Reimbursements",
            [
                "Submit under <strong>Reimbursements</strong> with the amount, what it was for, and a photo or PDF "
                "of the receipt.",
                "Receipts must be JPG, PNG, GIF, WEBP or PDF, and under 5 MB.",
                "You will be emailed when your claim is approved or rejected.",
            ],
        )
    )


def _closing(role_line: str) -> str:
    return (
        _callout(
            "<strong>Found a bug, or something that does not match what this email describes?</strong><br>"
            f"Reply to this email, or write to <a href=\"mailto:{SUPPORT_EMAIL}\" style=\"color:{BRAND_RED};\">{SUPPORT_EMAIL}</a>. "
            "Please include what you were doing, what you expected, and what happened instead — a screenshot helps "
            "a great deal. Reports are collected and passed to the development team for fixing, so telling us costs "
            "you a minute and saves everyone else the same problem."
        )
        + f'<p style="margin:0 0 6px 0;color:{TEXT_MUTED};font-size:13px;line-height:1.6;">{role_line}</p>'
        + f'<p style="margin:0;color:{TEXT_MUTED};font-size:13px;line-height:1.6;">'
        f"This is the first week the portal is in real use. Expect some rough edges, and please report them.</p>"
    )


def _body(full_name: str, intro: str, role_blocks: str, role_line: str) -> str:
    return _email_shell(
        f"""
          <tr>
            <td style="padding: 8px 40px 40px;">
              <h1 style="margin:24px 0 4px;color:{TEXT_HEADING};font-size:22px;font-weight:700;">Your {COMPANY_NAME} HRM portal is live</h1>
              <p style="margin:0 0 24px;color:{TEXT_MUTED};font-size:14px;">A short guide to what you can do in it.</p>
              <p style="margin:0 0 16px 0;color:{TEXT_BODY};font-size:16px;line-height:1.6;">Hi {full_name},</p>
              <p style="margin:0 0 8px 0;color:{TEXT_BODY};font-size:15px;line-height:1.6;">{intro}</p>
              {_common_blocks()}
              {role_blocks}
              {_closing(role_line)}
            </td>
          </tr>"""
    )


def build(full_name: str, role: str, is_hr: bool, reports: int) -> tuple:
    """Return (subject, html) for one person. `role` is the lowercase role value."""
    admin = role == "admin"
    hr_manager = role == "manager" and is_hr
    plain_manager = role == "manager" and not is_hr

    if admin:
        intro = (
            "Attendance, leave, reimbursements and payroll now run through the HRM portal. "
            "As an administrator you also approve what everyone else submits, so this covers both."
        )
        blocks = _section(
            "6. What you approve",
            [
                "<strong>Leave</strong> — approve or reject under Leave Requests. Approving draws the days from that "
                "employee's balance automatically, and the system refuses an approval that would exceed their "
                "allowance rather than letting the balance go negative.",
                "<strong>Reimbursements</strong> — approve or reject claims; the employee is emailed either way.",
                "<strong>Payroll and payslips</strong> — generate runs, review entries and publish payslips.",
                "<strong>People</strong> — create employees, set roles and salaries, reset a forgotten password, and "
                "deactivate someone who has left.",
            ],
        ) + _section(
            "7. Two things to know as an administrator",
            [
                "Every role change, salary change, leave decision, password reset and login is now recorded in an "
                "audit trail with your name against it.",
                "There is no self-service signup. New staff exist only when you create them, and they receive their "
                "password by email automatically.",
            ],
        )
        role_line = "You are set up as an administrator."

    elif hr_manager:
        intro = (
            "Attendance, leave, reimbursements and payroll now run through the HRM portal. "
            "You hold the HR role, so you also approve what the team submits."
        )
        blocks = _section(
            "6. What you approve",
            [
                "<strong>Leave</strong> — approve or reject under Leave Requests. Approving draws the days from that "
                "employee's balance automatically and cannot push it past their allowance.",
                "<strong>Reimbursements</strong> — approve or reject claims; the employee is emailed either way.",
                "<strong>People</strong> — create employees, update employment details and reset a forgotten password.",
                "You cannot modify administrator accounts or grant the administrator role — that stays with an admin.",
            ],
        )
        role_line = "You are set up as a manager with HR permissions."

    elif plain_manager:
        team = (
            f"You have {reports} team member{'s' if reports != 1 else ''} reporting to you."
            if reports
            else "You do not currently have anyone reporting to you."
        )
        intro = (
            "Attendance, leave, reimbursements and payroll now run through the HRM portal. "
            f"{team} This covers your own use and what you can do for the team."
        )
        blocks = _section(
            "6. What you can do for your team",
            [
                "<strong>See your team</strong> — their profiles, attendance and hours, under Team.",
                "<strong>Approve reimbursements</strong> — review and decide on claims.",
                "<strong>Post announcements</strong> — visible to everyone in the company.",
                "<strong>Leave requests are not yours to approve.</strong> Leave is decided by an administrator or HR, "
                "so your team's requests go to them, not to you. Please do not promise approval on the spot.",
                "Salary, bank details and CNIC numbers are deliberately hidden from you on your team's profiles. "
                "Blank there means hidden by design, not missing data.",
            ],
        )
        role_line = "You are set up as a manager."

    else:
        intro = (
            "Attendance, leave and reimbursements now run through the HRM portal instead of messages and "
            "spreadsheets. Here is everything you need."
        )
        blocks = ""
        role_line = "You are set up as an employee."

    subject = f"Your {COMPANY_NAME} HRM portal — how to use it"
    return subject, _body(full_name, intro, blocks, role_line)


def send_to(user) -> str:
    reports = len(getattr(user, "subordinates", []) or [])
    role = user.role.value if hasattr(user.role, "value") else str(user.role).lower()
    subject, html = build(user.full_name, role, bool(user.is_hr), reports)
    send_email([user.email], subject, html, html=True, reply_to=SUPPORT_EMAIL)
    return subject
