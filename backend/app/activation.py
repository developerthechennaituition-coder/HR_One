import hashlib
import os
import smtplib
from email.message import EmailMessage
from urllib.parse import quote

from dotenv import load_dotenv

load_dotenv()


def hash_activation_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def build_activation_url(token: str) -> str:
    frontend_url = os.getenv("FRONTEND_URL", "http://localhost:5173").rstrip("/")
    return f"{frontend_url}/activate?token={quote(token)}"


def send_activation_email(recipient: str, name: str, activation_url: str) -> str:
    smtp_host = os.getenv("SMTP_HOST", "").strip()
    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    smtp_username = os.getenv("SMTP_USERNAME", "").strip()
    smtp_password = os.getenv("SMTP_PASSWORD", "")
    smtp_from = os.getenv("SMTP_FROM", "").strip() or smtp_username

    if not smtp_host or not smtp_from:
        print(f"[DEV ONLY] Activation link for {recipient}: {activation_url}")
        return "development_console"

    message = EmailMessage()
    message["Subject"] = "Activate your HRMS account"
    message["From"] = smtp_from
    message["To"] = recipient
    message.set_content(
        f"Hello {name},\n\nActivate your HRMS account here:\n{activation_url}\n\n"
        "This link expires in 24 hours."
    )

    with smtplib.SMTP(smtp_host, smtp_port, timeout=15) as smtp:
        smtp.starttls()
        if smtp_username:
            smtp.login(smtp_username, smtp_password)
        smtp.send_message(message)
    return "smtp"


def send_hr_email_with_attachment(
    recipients: list[str],
    subject: str,
    body: str,
    filename: str,
    content: bytes,
    content_type: str,
) -> str:
    smtp_host = os.getenv("SMTP_HOST", "").strip()
    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    smtp_username = os.getenv("SMTP_USERNAME", "").strip()
    smtp_password = os.getenv("SMTP_PASSWORD", "")
    smtp_from = os.getenv("SMTP_FROM", "").strip() or smtp_username

    if not smtp_host or not smtp_from:
        raise RuntimeError("SMTP_HOST and SMTP_FROM (or SMTP_USERNAME) must be configured.")
    if not recipients:
        raise ValueError("At least one HR recipient is required.")

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = smtp_from
    message["To"] = "undisclosed-recipients:;"
    message.set_content(body)
    maintype, subtype = content_type.split("/", 1)
    message.add_attachment(content, maintype=maintype, subtype=subtype, filename=filename)

    with smtplib.SMTP(smtp_host, smtp_port, timeout=15) as smtp:
        smtp.starttls()
        if smtp_username:
            smtp.login(smtp_username, smtp_password)
        refused = smtp.send_message(message, to_addrs=recipients)
    if refused:
        raise smtplib.SMTPRecipientsRefused(refused)
    return "smtp"
