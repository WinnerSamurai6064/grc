"""
Green Recruiters - SMTP Service
Provider-independent email sending. Switching providers (Zoho, Gmail,
SendGrid SMTP relay, etc.) should only require editing .env - no code changes.

Handles:
  - OTP login emails
  - CMS-composed emails (To/CC/BCC, HTML body, attachments, logo)
"""

import mimetypes
import os
import smtplib
from email.message import EmailMessage
from email.utils import formataddr
from typing import Optional

import db

SMTP_HOST = os.environ["SMTP_HOST"]
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USERNAME = os.environ["SMTP_USERNAME"]
SMTP_PASSWORD = os.environ["SMTP_PASSWORD"]
SMTP_USE_TLS = os.environ.get("SMTP_USE_TLS", "true").lower() == "true"

FROM_EMAIL = os.environ.get("SMTP_FROM_EMAIL", SMTP_USERNAME)
FROM_NAME = os.environ.get("SMTP_FROM_NAME", "Green Recruiters")


def _connect() -> smtplib.SMTP:
    """Open and authenticate an SMTP connection based on .env settings."""
    smtp = smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20)
    if SMTP_USE_TLS:
        smtp.starttls()
    smtp.login(SMTP_USERNAME, SMTP_PASSWORD)
    return smtp


def _log_email(recipient: str, subject: str, status_text: str) -> None:
    db.execute(
        """
        INSERT INTO email_log (recipient, subject, status, smtp_provider)
        VALUES (%s, %s, %s, %s)
        """,
        (recipient, subject, status_text, SMTP_HOST),
    )


def send_otp_email(to_email: str, otp: str, ttl_minutes: int) -> None:
    """Send a one-time login code to an administrator."""
    message = EmailMessage()
    message["Subject"] = "Your Green Recruiters login code"
    message["From"] = formataddr((FROM_NAME, FROM_EMAIL))
    message["To"] = to_email

    message.set_content(
        f"Your login code is: {otp}\n\n"
        f"This code expires in {ttl_minutes} minutes.\n"
        f"If you did not request this, you can safely ignore this email."
    )

    message.add_alternative(
        f"""
        <html>
          <body style="font-family: Arial, sans-serif; color: #222;">
            <p>Your Green Recruiters login code is:</p>
            <p style="font-size: 28px; font-weight: bold; letter-spacing: 4px;">{otp}</p>
            <p style="color: #666;">This code expires in {ttl_minutes} minutes.</p>
            <p style="color: #999; font-size: 12px;">
              If you did not request this, you can safely ignore this email.
            </p>
          </body>
        </html>
        """,
        subtype="html",
    )

    try:
        with _connect() as smtp:
            smtp.send_message(message)
        _log_email(to_email, message["Subject"], "sent")
    except Exception as exc:
        _log_email(to_email, message["Subject"], f"failed: {exc}")
        raise


def send_cms_email(
    to_addresses: list[str],
    subject: str,
    html_body: str,
    cc_addresses: Optional[list[str]] = None,
    bcc_addresses: Optional[list[str]] = None,
    attachments: Optional[list[dict]] = None,
) -> None:
    """
    Send an email composed from the CMS email panel.

    attachments: list of dicts, each with keys:
        - filename: str
        - content: bytes
        - content_type: str (optional, guessed from filename if omitted)
    """
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = formataddr((FROM_NAME, FROM_EMAIL))
    message["To"] = ", ".join(to_addresses)

    if cc_addresses:
        message["Cc"] = ", ".join(cc_addresses)

    all_recipients = list(to_addresses)
    if cc_addresses:
        all_recipients += cc_addresses
    if bcc_addresses:
        all_recipients += bcc_addresses

    message.set_content("This email requires an HTML-capable email client to view.")
    message.add_alternative(html_body, subtype="html")

    for attachment in attachments or []:
        content_type = attachment.get("content_type")
        if not content_type:
            guessed, _ = mimetypes.guess_type(attachment["filename"])
            content_type = guessed or "application/octet-stream"

        maintype, _, subtype = content_type.partition("/")
        message.add_attachment(
            attachment["content"],
            maintype=maintype,
            subtype=subtype or "octet-stream",
            filename=attachment["filename"],
        )

    try:
        with _connect() as smtp:
            smtp.send_message(message, to_addrs=all_recipients)
        _log_email(", ".join(to_addresses), subject, "sent")
    except Exception as exc:
        _log_email(", ".join(to_addresses), subject, f"failed: {exc}")
        raise
