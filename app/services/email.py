import logging
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from html import escape

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


def _env_value(name: str) -> str | None:
    value = os.getenv(name)
    if value is None or value == "":
        return None
    return value


def _env_int(name: str, default: int) -> int:
    raw = _env_value(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = _env_value(name)
    if raw is None:
        return default
    return raw.lower() in {"1", "true", "yes", "on"}


def _get_smtp_config() -> dict:
    return {
        "host": _env_value("SMTP_HOST") or "localhost",
        "port": _env_int("SMTP_PORT", 587),
        "username": _env_value("SMTP_USERNAME"),
        "password": _env_value("SMTP_PASSWORD"),
        "use_tls": _env_bool("SMTP_USE_TLS", True),
        "use_ssl": _env_bool("SMTP_USE_SSL", False),
        "from_email": _env_value("SMTP_FROM_EMAIL") or "noreply@example.com",
        "from_name": _env_value("SMTP_FROM_NAME") or "DotMac Voice",
    }


def send_email(
    _db: Session | None,
    to_email: str,
    subject: str,
    body_html: str,
    body_text: str | None = None,
) -> bool:
    config = _get_smtp_config()
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"{config['from_name']} <{config['from_email']}>"
    msg["To"] = to_email

    if body_text:
        msg.attach(MIMEText(body_text, "plain"))
    msg.attach(MIMEText(body_html, "html"))

    try:
        if config["use_ssl"]:
            server = smtplib.SMTP_SSL(config["host"], config["port"])
        else:
            server = smtplib.SMTP(config["host"], config["port"])

        if config["use_tls"] and not config["use_ssl"]:
            server.starttls()

        if config["username"] and config["password"]:
            server.login(config["username"], config["password"])

        server.sendmail(config["from_email"], to_email, msg.as_string())
        server.quit()

        logger.info("Email sent to %s", to_email)
        return True
    except Exception as exc:
        logger.error("Failed to send email to %s: %s", to_email, exc)
        return False


def send_password_reset_email(
    db: Session | None,
    to_email: str,
    reset_token: str,
    person_name: str | None = None,
) -> bool:
    name = person_name or "there"
    app_url = _env_value("APP_URL") or "http://localhost:8000"
    reset_link = f"{app_url.rstrip('/')}/auth/reset-password?token={reset_token}"
    subject = "Reset your password"
    html_name = escape(name)
    html_reset_link = escape(reset_link, quote=True)
    body_html = (
        f"<p>Hi {html_name},</p>"
        "<p>Use the link below to reset your password:</p>"
        f'<p><a href="{html_reset_link}">Reset password</a></p>'
    )
    body_text = f"Hi {name}, use this link to reset your password: {reset_link}"
    return send_email(db, to_email, subject, body_html, body_text)
