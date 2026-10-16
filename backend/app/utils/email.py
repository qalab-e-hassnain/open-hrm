import logging
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import List
from app.config import settings
from app.utils.email_assets import LOGO_PNG_BASE64

logger = logging.getLogger(__name__)

# Read from settings rather than hardcoded: this file previously carried its
# own literal, so emails signed off with a different company name than the one
# printed on payslips and tax certificates.
COMPANY_NAME = settings.COMPANY_NAME
BRAND_NAME = settings.BRAND_NAME

# Mirrors frontend/src/index.css's brand tokens so transactional email looks
# like the rest of the product instead of a generic blue/slate template.
BRAND_RED = "#C0392B"
BRAND_RED_DARK = "#A93226"
BRAND_RED_SOFT = "#FBEAE8"
ACCENT_GREEN = "#10B981"
# Matches --ps-success in frontend/src/pages/employee/Payslips.css exactly —
# that's the specific green the payslip page itself uses for net pay.
PAYSLIP_GREEN = "#12A66A"
TEXT_HEADING = "#0A0A0A"
TEXT_BODY = "#334155"
TEXT_MUTED = "#64748B"
BORDER_LIGHT = "#DEDFE3"
BG_PAGE = "#F4F6F8"


def _email_header() -> str:
    """
    The deployment's own logo and name, reproducing the app sidebar's brand
    block so the email reads as the same product rather than a generic
    template.

    Both come from settings, because this system is handed to other companies
    to run for their own staff and a logo compiled into the source cannot be
    theirs. With no BRAND_LOGO_URL configured the name stands alone, which is
    the right answer for a tenant who has not supplied one — better than
    somebody else's mark on their mail.
    """
    logo = ""
    if settings.BRAND_LOGO_URL:
        logo = f"""
                  <td>
                    <img src="{settings.BRAND_LOGO_URL}" width="40" height="40" alt="{BRAND_NAME}" style="display: block; width: 40px; height: 40px; border-radius: 50%;">
                  </td>"""
    return f"""
          <tr>
            <td style="padding: 28px 40px 22px; border-bottom: 1px solid {BORDER_LIGHT};">
              <table role="presentation" cellspacing="0" cellpadding="0">
                <tr>{logo}
                  <td style="{'padding-left: 12px;' if logo else ''}">
                    <div style="color: {TEXT_HEADING}; font-size: 16px; font-weight: 700; letter-spacing: -0.01em;">{BRAND_NAME}</div>
                  </td>
                </tr>
              </table>
            </td>
          </tr>"""


def _email_footer() -> str:
    return f"""
          <tr>
            <td style="padding: 22px 40px; background-color: {BG_PAGE}; border-top: 1px solid {BORDER_LIGHT};">
              <p style="margin: 0 0 6px; color: {TEXT_MUTED}; font-size: 13px;">Best regards,<br><strong style="color: {TEXT_HEADING};">{COMPANY_NAME} HR Team</strong></p>
              <p style="margin: 0; color: {TEXT_MUTED}; font-size: 12px;"><a href="{settings.PORTAL_URL}" style="color: {TEXT_MUTED};">{settings.PORTAL_URL}</a></p>
            </td>
          </tr>"""


def _email_shell(body_html: str) -> str:
    """Wraps a template's content in the shared card/header/footer chrome."""
    return f"""
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
</head>
<body style="margin: 0; padding: 0; font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background-color: {BG_PAGE};">
  <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background-color: {BG_PAGE}; padding: 40px 20px;">
    <tr>
      <td align="center">
        <table role="presentation" width="600" cellspacing="0" cellpadding="0" style="background-color: #ffffff; border-radius: 12px; box-shadow: 0 4px 20px rgba(192,57,43,0.08); overflow: hidden;">
{_email_header()}
{body_html}
{_email_footer()}
        </table>
      </td>
    </tr>
  </table>
</body>
</html>
"""


def _status_pill(label: str, is_positive: bool) -> str:
    color = ACCENT_GREEN if is_positive else BRAND_RED
    return f'<span style="color: {color}; font-weight: 700;">{label}</span>'


def _button(label: str, href: str) -> str:
    """
    A call-to-action that works in Outlook.

    A table with a background colour rather than a styled anchor: Outlook on
    Windows renders through Word, which ignores padding and background on an
    <a>, leaving a bare blue link where the button should be.
    """
    return f"""
              <table role="presentation" cellspacing="0" cellpadding="0" style="margin: 0 0 24px;">
                <tr>
                  <td align="center" style="background-color: {BRAND_RED}; border-radius: 8px;">
                    <a href="{href}" target="_blank"
                       style="display: inline-block; padding: 13px 28px; color: #ffffff;
                              font-size: 15px; font-weight: 600; text-decoration: none;">{label}</a>
                  </td>
                </tr>
              </table>"""


def _welcome_email_html(full_name: str, to_email: str, password: str) -> str:
    body = f"""
          <tr>
            <td style="padding: 8px 40px 40px;">
              <h1 style="margin: 24px 0 4px; color: {TEXT_HEADING}; font-size: 22px; font-weight: 700;">Welcome to {COMPANY_NAME}</h1>
              <p style="margin: 0 0 24px; color: {TEXT_MUTED}; font-size: 14px;">Your account is ready.</p>
              <p style="margin: 0 0 20px 0; color: {TEXT_BODY}; font-size: 16px; line-height: 1.6;">Hi {full_name},</p>
              <p style="margin: 0 0 24px 0; color: {TEXT_BODY}; font-size: 15px; line-height: 1.6;">Your account has been successfully created in the {COMPANY_NAME} HRM portal.</p>
              <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background-color: {BRAND_RED_SOFT}; border-radius: 8px; border: 1px solid {BORDER_LIGHT}; margin-bottom: 24px;">
                <tr>
                  <td style="padding: 20px; border-left: 3px solid {BRAND_RED};">
                    <p style="margin: 0 0 12px 0; color: {TEXT_MUTED}; font-size: 12px; text-transform: uppercase; letter-spacing: 0.5px; font-weight: 600;">Login credentials</p>
                    <p style="margin: 0 0 8px 0; color: {TEXT_HEADING}; font-size: 15px;"><strong>Email:</strong> {to_email}</p>
                    <p style="margin: 0; color: {TEXT_HEADING}; font-size: 15px;"><strong>Password:</strong> {password}</p>
                  </td>
                </tr>
              </table>
              <p style="margin: 0 0 20px 0; color: {TEXT_BODY}; font-size: 15px; line-height: 1.6;">Sign in, change your password, and complete your profile — payroll and your documents depend on it.</p>
              {_button("Sign in to the portal", settings.PORTAL_URL)}
              <p style="margin: 0 0 24px 0; color: {TEXT_MUTED}; font-size: 13px; line-height: 1.6;">If the button does not work, open <a href="{settings.PORTAL_URL}" style="color: {BRAND_RED};">{settings.PORTAL_URL}</a></p>
              <p style="margin: 0; color: {TEXT_BODY}; font-size: 15px; line-height: 1.6;">If you have any questions, please reach out to your administrator or the HR team.</p>
            </td>
          </tr>"""
    return _email_shell(body)


def _welcome_email_plain(full_name: str, to_email: str, password: str) -> str:
    return f"""Hi {full_name},

Your account has been successfully created in the {COMPANY_NAME} HRM portal.

Login Credentials:
  Email: {to_email}
  Password: {password}

Sign in here: {settings.PORTAL_URL}

Please log in and update your profile as needed. We recommend changing your password after your first login.

Best regards,
{COMPANY_NAME} HR Team
"""


def send_welcome_email(to_email: str, full_name: str, password: str):
    subject = f"Welcome to {COMPANY_NAME} HRM"
    html_body = _welcome_email_html(full_name, to_email, password)
    plain_body = _welcome_email_plain(full_name, to_email, password)
    send_email([to_email], subject, html_body, html=True, plain_fallback=plain_body)
    print("---- Email Preview ----")
    print("To:", to_email)
    print("Subject:", subject)
    print("-----------------------")


def send_email(to_emails: List[str], subject: str, body: str, html: bool = False,
               plain_fallback: str = None, reply_to: str = None):
    try:
        from_address = settings.SMTP_FROM_EMAIL or settings.SMTP_USER
        msg = MIMEMultipart("alternative")
        msg['From'] = f"{COMPANY_NAME} <{from_address}>"
        msg['To'] = ", ".join(to_emails)
        msg['Subject'] = subject
        # The From address is a no-reply mailbox, so anything that invites a
        # reply must name where that reply should actually land.
        if reply_to:
            msg['Reply-To'] = reply_to

        if html and plain_fallback:
            msg.attach(MIMEText(plain_fallback, 'plain'))
            msg.attach(MIMEText(body, 'html'))
        elif html:
            msg.attach(MIMEText(body, 'html'))
        else:
            msg.attach(MIMEText(body, 'plain'))

        server = smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT)
        server.starttls()
        server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
        server.send_message(msg)
        server.quit()

        logger.info("Email sent successfully to %s", to_emails)
    except Exception:
        # Not re-raised: a failed send must not blow up the request that
        # triggered it (leave approval, payslip generation, etc.) — the
        # caller already did its job, the email is a side effect. Plain
        # ASCII in the log line, not an emoji: this runs on Windows dev
        # boxes too, whose default console encoding (cp1252) can't encode
        # one, which previously turned a clean "failed to send" log line
        # into an unhandled UnicodeEncodeError.
        logger.exception("Failed to send email to %s", to_emails)


def _reimbursement_email_html(status: str, amount: float) -> str:
    status_lower = status.lower()
    is_approved = status_lower in ("approved", "accept")
    body = f"""
          <tr>
            <td style="padding: 8px 40px 40px;">
              <h1 style="margin: 24px 0 20px; color: {TEXT_HEADING}; font-size: 20px; font-weight: 700;">Reimbursement Request Update</h1>
              <p style="margin: 0 0 20px 0; color: {TEXT_BODY}; font-size: 16px; line-height: 1.6;">Dear Employee,</p>
              <p style="margin: 0 0 24px 0; color: {TEXT_BODY}; font-size: 15px; line-height: 1.6;">Your reimbursement request for <strong>PKR {amount:,.2f}</strong> has been {_status_pill(status, is_approved)}.</p>
              <p style="margin: 0; color: {TEXT_BODY}; font-size: 15px; line-height: 1.6;">Please check your HRM dashboard for more details.</p>
            </td>
          </tr>"""
    return _email_shell(body)


def send_reimbursement_approval_email(employee_email: str, status: str, amount: float):
    """Send reimbursement approval/rejection notification"""
    subject = f"Reimbursement Request {status.title()}"
    html_body = _reimbursement_email_html(status, amount)
    plain_body = f"""Dear Employee,

Your reimbursement request for PKR {amount:,.2f} has been {status}.

Please check your HRM dashboard for more details.

Best regards,
{COMPANY_NAME} HR Team
"""
    send_email([employee_email], subject, html_body, html=True, plain_fallback=plain_body)


def _leave_email_html(leave_type: str, start_date: str, end_date: str, status: str, rejection_reason: str = None) -> str:
    is_approved = status == "approved"
    reason_block = ""
    if rejection_reason:
        reason_block = f"""
              <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background-color: {BRAND_RED_SOFT}; border-radius: 8px; border: 1px solid {BORDER_LIGHT}; margin-bottom: 24px;">
                <tr>
                  <td style="padding: 16px 20px; border-left: 3px solid {BRAND_RED};">
                    <p style="margin: 0 0 6px 0; color: {TEXT_MUTED}; font-size: 12px; text-transform: uppercase; letter-spacing: 0.5px; font-weight: 600;">Reason</p>
                    <p style="margin: 0; color: {TEXT_HEADING}; font-size: 15px;">{rejection_reason}</p>
                  </td>
                </tr>
              </table>"""
    body = f"""
          <tr>
            <td style="padding: 8px 40px 40px;">
              <h1 style="margin: 24px 0 20px; color: {TEXT_HEADING}; font-size: 20px; font-weight: 700;">Leave Request Update</h1>
              <p style="margin: 0 0 20px 0; color: {TEXT_BODY}; font-size: 16px; line-height: 1.6;">Dear Employee,</p>
              <p style="margin: 0 0 24px 0; color: {TEXT_BODY}; font-size: 15px; line-height: 1.6;">Your <strong>{leave_type}</strong> leave request from <strong>{start_date}</strong> to <strong>{end_date}</strong> has been {_status_pill(status, is_approved)}.</p>
              {reason_block}
              <p style="margin: 0; color: {TEXT_BODY}; font-size: 15px; line-height: 1.6;">Please check your HRM dashboard for more details.</p>
            </td>
          </tr>"""
    return _email_shell(body)


def _payslip_email_html(doc, month_label: str) -> str:
    """doc is the PayslipDocument built by build_payslip_document() in payslips.py."""
    def _rows(lines):
        return "".join(
            f'<tr><td style="padding: 6px 0; color: {TEXT_BODY}; font-size: 14px;">{l.label}</td>'
            f'<td style="padding: 6px 0; color: {TEXT_HEADING}; font-size: 14px; text-align: right; font-variant-numeric: tabular-nums;">{doc.currency} {l.amount:,.2f}</td></tr>'
            for l in lines
        )
    body = f"""
          <tr>
            <td style="padding: 8px 40px 40px;">
              <h1 style="margin: 24px 0 4px; color: {TEXT_HEADING}; font-size: 20px; font-weight: 700;">Payslip — {month_label}</h1>
              <p style="margin: 0 0 24px; color: {TEXT_MUTED}; font-size: 13px;">{doc.company.name} &middot; Pay period {doc.period_label}</p>

              <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="margin-bottom: 20px;">
                <tr><td colspan="2" style="padding-bottom: 8px; color: {TEXT_MUTED}; font-size: 12px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.04em; border-bottom: 1px solid {BORDER_LIGHT};">Earnings</td></tr>
                {_rows(doc.earnings)}
                <tr><td style="padding-top: 8px; color: {TEXT_HEADING}; font-size: 14px; font-weight: 700;">Gross</td><td style="padding-top: 8px; color: {TEXT_HEADING}; font-size: 14px; font-weight: 700; text-align: right;">{doc.currency} {doc.gross_earnings:,.2f}</td></tr>
              </table>

              <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="margin-bottom: 24px;">
                <tr><td colspan="2" style="padding-bottom: 8px; color: {TEXT_MUTED}; font-size: 12px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.04em; border-bottom: 1px solid {BORDER_LIGHT};">Deductions</td></tr>
                {_rows(doc.deductions)}
                <tr><td style="padding-top: 8px; color: {TEXT_HEADING}; font-size: 14px; font-weight: 700;">Total</td><td style="padding-top: 8px; color: {TEXT_HEADING}; font-size: 14px; font-weight: 700; text-align: right;">{doc.currency} {doc.total_deductions:,.2f}</td></tr>
              </table>

              <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background-color: #EAFBF3; border-radius: 8px; border: 1px solid {BORDER_LIGHT}; margin-bottom: 20px;">
                <tr>
                  <td style="padding: 18px 20px; border-left: 3px solid {PAYSLIP_GREEN};">
                    <p style="margin: 0 0 4px 0; color: {TEXT_MUTED}; font-size: 12px; text-transform: uppercase; letter-spacing: 0.5px; font-weight: 600;">Net pay</p>
                    <p style="margin: 0; color: {PAYSLIP_GREEN}; font-size: 22px; font-weight: 800;">{doc.currency} {doc.net_salary:,.2f}</p>
                  </td>
                </tr>
              </table>

              <p style="margin: 0; color: {TEXT_MUTED}; font-size: 12px; line-height: 1.6;">Verification code: {doc.verification_code or '-'}<br>This is a computer generated payslip and does not require a signature.</p>
            </td>
          </tr>"""
    return _email_shell(body)


def send_payslip_email(to_email: str, doc, month_label: str):
    """doc is the PayslipDocument from build_payslip_document(). Mirrors the
    plain-text summary that used to be the only version of this email, now
    with the same branded HTML shell as the other transactional emails."""
    lines = "\n".join(f"  {l.label}: {doc.currency} {l.amount:,.2f}" for l in doc.earnings)
    cuts = "\n".join(f"  {l.label}: {doc.currency} {l.amount:,.2f}" for l in doc.deductions)
    plain_body = (
        f"Payslip {doc.payslip_no or ''} — {month_label}\n"
        f"{doc.company.name}\n\n"
        f"Pay period: {doc.period_label}\n\n"
        f"Earnings:\n{lines}\n  Gross: {doc.currency} {doc.gross_earnings:,.2f}\n\n"
        f"Deductions:\n{cuts}\n  Total: {doc.currency} {doc.total_deductions:,.2f}\n\n"
        f"NET PAY: {doc.currency} {doc.net_salary:,.2f}\n\n"
        f"Verification code: {doc.verification_code or '-'}\n"
        "This is a computer generated payslip and does not require a signature.\n"
    )
    html_body = _payslip_email_html(doc, month_label)
    send_email([to_email], f"Payslip — {month_label}", html_body, html=True, plain_fallback=plain_body)


def _leave_reminder_row(item: dict, is_overdue: bool) -> str:
    date_range = item["start_date"].strftime("%d %b %Y")
    if item["end_date"] != item["start_date"]:
        date_range += f" – {item['end_date'].strftime('%d %b %Y')}"
    tag = (
        f'<span style="color: {BRAND_RED}; font-weight: 700; font-size: 12px;">'
        f'{item["days_overdue"]} day{"s" if item["days_overdue"] != 1 else ""} overdue</span>'
        if is_overdue else
        f'<span style="color: {TEXT_MUTED}; font-weight: 700; font-size: 12px;">Starts tomorrow</span>'
    )
    return f"""
              <tr>
                <td style="padding: 12px 0; border-bottom: 1px solid {BORDER_LIGHT};">
                  <p style="margin: 0 0 2px; color: {TEXT_HEADING}; font-size: 14px; font-weight: 700;">{item['employee_name']} &mdash; {item['leave_type'].title()} Leave</p>
                  <p style="margin: 0; color: {TEXT_MUTED}; font-size: 13px;">{date_range}</p>
                </td>
                <td style="padding: 12px 0; border-bottom: 1px solid {BORDER_LIGHT}; text-align: right; white-space: nowrap;">{tag}</td>
              </tr>"""


def _leave_reminder_section(title: str, subtitle: str, items: list) -> str:
    if not items:
        return ""
    is_overdue = "overdue" in title.lower()
    rows = "".join(_leave_reminder_row(item, is_overdue) for item in items)
    return f"""
              <p style="margin: 24px 0 4px; color: {TEXT_HEADING}; font-size: 15px; font-weight: 700;">{title}</p>
              <p style="margin: 0 0 8px; color: {TEXT_MUTED}; font-size: 13px;">{subtitle}</p>
              <table role="presentation" width="100%" cellspacing="0" cellpadding="0">
                {rows}
              </table>"""


def _leave_reminder_email_html(overdue: list, upcoming: list) -> str:
    body = f"""
          <tr>
            <td style="padding: 8px 40px 40px;">
              <h1 style="margin: 24px 0 4px; color: {TEXT_HEADING}; font-size: 20px; font-weight: 700;">Leave requests need a decision</h1>
              <p style="margin: 0 0 8px; color: {TEXT_MUTED}; font-size: 14px;">Daily reminder from the {COMPANY_NAME} HRM portal.</p>
              {_leave_reminder_section("Missed — action overdue", "The requested start date has already passed and this is still awaiting a decision.", overdue)}
              {_leave_reminder_section("Starting tomorrow", "Still awaiting a decision, and the requested leave begins tomorrow.", upcoming)}
              <p style="margin: 24px 0 0; color: {TEXT_BODY}; font-size: 14px; line-height: 1.6;">Open Manage Leaves in the HRM portal to approve or reject these requests.</p>
            </td>
          </tr>"""
    return _email_shell(body)


def send_leave_reminder_email(admin_emails: List[str], overdue: list, upcoming: list):
    """Daily admin reminder for leave requests still undecided past their start
    date, or due to start tomorrow. `overdue`/`upcoming` are lists of dicts
    with employee_name, leave_type, start_date, end_date (and days_overdue for
    the overdue list) — see app/utils/leave_reminders.py."""
    if not admin_emails or (not overdue and not upcoming):
        return
    subject_bits = []
    if overdue:
        subject_bits.append(f"{len(overdue)} overdue")
    if upcoming:
        subject_bits.append(f"{len(upcoming)} due tomorrow")
    subject = f"Leave reminder: {', '.join(subject_bits)} awaiting your decision"

    html_body = _leave_reminder_email_html(overdue, upcoming)

    def _plain_lines(items, is_overdue):
        lines = []
        for item in items:
            date_range = item["start_date"].strftime("%d %b %Y")
            if item["end_date"] != item["start_date"]:
                date_range += f" - {item['end_date'].strftime('%d %b %Y')}"
            tag = f"{item['days_overdue']} day(s) overdue" if is_overdue else "starts tomorrow"
            lines.append(f"  - {item['employee_name']} — {item['leave_type'].title()} Leave, {date_range} ({tag})")
        return "\n".join(lines)

    plain_body = "Leave requests need a decision:\n\n"
    if overdue:
        plain_body += "MISSED - ACTION OVERDUE:\n" + _plain_lines(overdue, True) + "\n\n"
    if upcoming:
        plain_body += "STARTING TOMORROW:\n" + _plain_lines(upcoming, False) + "\n\n"
    plain_body += f"Open Manage Leaves in the HRM portal to approve or reject these requests.\n\nBest regards,\n{COMPANY_NAME} HR Team\n"

    send_email(admin_emails, subject, html_body, html=True, plain_fallback=plain_body)


def _payslip_dispute_email_html(employee_name: str, category_label: str, comment: str, period_label: str, attachment_url: str = None) -> str:
    attachment_block = ""
    if attachment_url:
        attachment_block = f"""
              <p style="margin: 0 0 24px; color: {TEXT_BODY}; font-size: 14px;"><strong>Supporting document:</strong> <a href="{attachment_url}" style="color: {BRAND_RED};">{attachment_url}</a></p>"""
    body = f"""
          <tr>
            <td style="padding: 8px 40px 40px;">
              <h1 style="margin: 24px 0 4px; color: {TEXT_HEADING}; font-size: 20px; font-weight: 700;">Payslip query raised</h1>
              <p style="margin: 0 0 24px; color: {TEXT_MUTED}; font-size: 13px;">{period_label} &middot; {employee_name}</p>
              <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background-color: {BRAND_RED_SOFT}; border-radius: 8px; border: 1px solid {BORDER_LIGHT}; margin-bottom: 24px;">
                <tr>
                  <td style="padding: 20px; border-left: 3px solid {BRAND_RED};">
                    <p style="margin: 0 0 6px 0; color: {TEXT_MUTED}; font-size: 12px; text-transform: uppercase; letter-spacing: 0.5px; font-weight: 600;">{category_label}</p>
                    <p style="margin: 0; color: {TEXT_HEADING}; font-size: 15px; line-height: 1.6;">{comment}</p>
                  </td>
                </tr>
              </table>{attachment_block}
              <p style="margin: 0; color: {TEXT_BODY}; font-size: 15px; line-height: 1.6;">Open Payslips &rarr; Payroll queries in the HRM portal to respond.</p>
            </td>
          </tr>"""
    return _email_shell(body)


def send_payslip_dispute_email(
    admin_emails: List[str], employee_name: str, category_label: str,
    comment: str, period_label: str, attachment_url: str = None,
):
    """Notify admin/HR by email the moment an employee raises a payslip query —
    the in-app notification alone is easy to miss if no one is looking at the
    dashboard when it comes in."""
    if not admin_emails:
        return
    subject = f"Payslip query: {employee_name} — {category_label}"
    html_body = _payslip_dispute_email_html(employee_name, category_label, comment, period_label, attachment_url)
    attachment_line = f"\nSupporting document: {attachment_url}\n" if attachment_url else ""
    plain_body = f"""Payslip query raised — {period_label}

Employee: {employee_name}
Issue: {category_label}

{comment}
{attachment_line}
Open Payslips -> Payroll queries in the HRM portal to respond.

Best regards,
{COMPANY_NAME} HR Team
"""
    send_email(admin_emails, subject, html_body, html=True, plain_fallback=plain_body)


def send_leave_status_email(
    employee_email: str,
    leave_type: str,
    start_date: str,
    end_date: str,
    status: str,
    rejection_reason: str = None,
):
    """Send leave approval/rejection notification. status is 'approved' or 'rejected'."""
    subject = f"Leave Request {status.title()}"
    html_body = _leave_email_html(leave_type, start_date, end_date, status, rejection_reason)
    reason_line = f"\nReason: {rejection_reason}\n" if rejection_reason else ""
    plain_body = f"""Dear Employee,

Your {leave_type} leave request from {start_date} to {end_date} has been {status}.
{reason_line}
Please check your HRM dashboard for more details.

Best regards,
{COMPANY_NAME} HR Team
"""
    send_email([employee_email], subject, html_body, html=True, plain_fallback=plain_body)
