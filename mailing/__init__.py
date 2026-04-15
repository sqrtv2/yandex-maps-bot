"""
Email sender module — sends emails via SMTP with account rotation.
"""
import smtplib
import ssl
import logging
import re
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.header import Header
from email.utils import formataddr
from datetime import datetime, date

from sqlalchemy.orm import Session

from app.models.mailing import SmtpAccount

logger = logging.getLogger(__name__)


def get_available_smtp_account(db: Session) -> SmtpAccount | None:
    """Get next available SMTP account that hasn't exceeded daily limit."""
    today = date.today().isoformat()

    # Reset counters for accounts from previous days
    stale = db.query(SmtpAccount).filter(
        SmtpAccount.is_active == True,
        SmtpAccount.last_reset_date != today
    ).all()
    for acc in stale:
        acc.sent_today = 0
        acc.last_reset_date = today
    if stale:
        db.commit()

    # Get account with the lowest usage that still has quota
    account = db.query(SmtpAccount).filter(
        SmtpAccount.is_active == True,
        SmtpAccount.sent_today < SmtpAccount.daily_limit
    ).order_by(SmtpAccount.sent_today.asc()).first()

    return account


def personalize_text(template: str, company_name: str, region: str = "",
                     category: str = "") -> str:
    """Replace template variables with company-specific data."""
    result = template
    result = result.replace("{company_name}", company_name or "")
    result = result.replace("{название}", company_name or "")
    result = result.replace("{город}", region or "")
    result = result.replace("{region}", region or "")
    result = result.replace("{категория}", category or "")
    result = result.replace("{category}", category or "")
    return result


def send_email(
    smtp_account: SmtpAccount,
    to_email: str,
    subject: str,
    body_html: str,
    body_text: str = None,
    sender_name: str = None,
) -> tuple[bool, str]:
    """
    Send a single email via SMTP.
    Returns (success: bool, error_message: str).
    """
    # Validate email format
    if not re.match(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$', to_email):
        return False, f"Invalid email format: {to_email}"

    try:
        msg = MIMEMultipart("alternative")
        from_display = sender_name or smtp_account.name or smtp_account.email
        msg["From"] = formataddr((str(Header(from_display, "utf-8")), smtp_account.email))
        msg["To"] = to_email
        msg["Subject"] = subject
        msg["List-Unsubscribe"] = f"<mailto:{smtp_account.email}?subject=unsubscribe>"
        msg["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
        msg["Precedence"] = "bulk"

        # Auto-generate plain text from HTML if not provided
        if not body_text and body_html:
            import html as _html
            _tmp = re.sub(r'<br\s*/?>|</p>|</div>|</tr>|</li>', '\n', body_html)
            _tmp = re.sub(r'<[^>]+>', '', _tmp)
            body_text = _html.unescape(_tmp).strip()
            # Collapse multiple blank lines
            body_text = re.sub(r'\n{3,}', '\n\n', body_text)

        # Plain text part (must go first in multipart/alternative)
        if body_text:
            msg.attach(MIMEText(body_text, "plain", "utf-8"))

        # HTML part  
        msg.attach(MIMEText(body_html, "html", "utf-8"))

        # Connect and send
        if smtp_account.use_ssl:
            context = ssl.create_default_context()
            with smtplib.SMTP_SSL(
                smtp_account.smtp_server,
                smtp_account.smtp_port,
                context=context,
                timeout=30
            ) as server:
                server.login(smtp_account.email, smtp_account.password)
                server.send_message(msg)
        else:
            with smtplib.SMTP(
                smtp_account.smtp_server,
                smtp_account.smtp_port,
                timeout=30
            ) as server:
                server.ehlo()
                server.starttls(context=ssl.create_default_context())
                server.ehlo()
                server.login(smtp_account.email, smtp_account.password)
                server.send_message(msg)

        logger.info(f"✉️ Email sent to {to_email} via {smtp_account.email}")
        return True, ""

    except smtplib.SMTPAuthenticationError as e:
        error = f"Auth failed for {smtp_account.email}: {e}"
        logger.error(error)
        return False, error
    except smtplib.SMTPRecipientsRefused as e:
        error = f"Recipient refused {to_email}: {e}"
        logger.error(error)
        return False, error
    except Exception as e:
        error = f"SMTP error sending to {to_email} via {smtp_account.email}: {e}"
        logger.error(error)
        return False, error


def test_smtp_account(account: SmtpAccount) -> tuple[bool, str]:
    """Test SMTP connection and authentication for an account."""
    try:
        if account.use_ssl:
            context = ssl.create_default_context()
            with smtplib.SMTP_SSL(
                account.smtp_server,
                account.smtp_port,
                context=context,
                timeout=15
            ) as server:
                server.login(account.email, account.password)
        else:
            with smtplib.SMTP(
                account.smtp_server,
                account.smtp_port,
                timeout=15
            ) as server:
                server.ehlo()
                server.starttls(context=ssl.create_default_context())
                server.ehlo()
                server.login(account.email, account.password)
        return True, "OK"
    except Exception as e:
        return False, str(e)
