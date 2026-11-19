import logging
import smtplib
from email.message import EmailMessage

from app.core.config import get_settings

log = logging.getLogger(__name__)


def send_password_reset_email(recipient: str, token: str) -> bool:
    s = get_settings()
    link = f"{s.frontend_url.rstrip('/')}/reset-password?token={token}"
    if not s.smtp_configured:
        log.warning("SMTP is not configured; password reset email was not sent.")
        if s.environment == "development":
            # Dev convenience only: lets you test the flow without SMTP. Never in production.
            log.warning("DEV reset link for %s: %s", recipient, link)
        return False
    message = EmailMessage()
    message["Subject"] = "Reset your urSarthi password"
    message["From"] = s.smtp_from
    message["To"] = recipient
    message.set_content(
        "A password reset was requested for your urSarthi account.\n\n"
        f"Use this link within {s.reset_token_minutes} minutes: {link}\n\n"
        "If you did not request this, you can ignore this email."
    )
    with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=15) as server:
        server.starttls()
        server.login(s.smtp_username, s.smtp_password)
        server.send_message(message)
    return True
