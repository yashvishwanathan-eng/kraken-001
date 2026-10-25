"""Sending invitation emails over SMTP (any provider: Gmail app password, Outlook, a school mail server).

email_mode (in .env):
  auto      live when SMTP_HOST and SMTP_FROM are set, otherwise off
  live      really send; a missing setting is logged as a failure
  simulate  pretend: the message is logged, nothing leaves the laptop (rehearsals, tests)
  off       nothing is sent; the admin shares the link or QR code by hand

The sender waits at most 5 s and retries once, like every other outside service.
"""

import logging
import smtplib
from email.message import EmailMessage

from app.config import get_settings

log = logging.getLogger("safetyai.email")

TIMEOUT_S = 5.0
ATTEMPTS = 2


class EmailSender:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []  # (to, subject) recorded in simulate mode

    @property
    def mode(self) -> str:
        s = get_settings()
        if s.email_mode == "auto":
            return "live" if (s.smtp_host and s.smtp_from) else "off"
        return s.email_mode

    def send(self, to: str, subject: str, body: str) -> str:
        """Returns sent, simulated, not_sent or off."""
        mode = self.mode
        if mode == "off":
            return "off"
        if mode == "simulate":
            self.sent.append((to, subject))
            log.info("email simulated to %s: %s", to, subject)
            return "simulated"
        s = get_settings()
        message = EmailMessage()
        message["From"], message["To"], message["Subject"] = s.smtp_from, to, subject
        message.set_content(body)
        for attempt in range(1, ATTEMPTS + 1):
            try:
                with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=TIMEOUT_S) as smtp:
                    smtp.starttls()
                    if s.smtp_user:
                        smtp.login(s.smtp_user, s.smtp_password)
                    smtp.send_message(message)
                log.info("email sent to %s", to)
                return "sent"
            except (smtplib.SMTPException, OSError) as exc:
                log.warning("email to %s attempt=%d failed: %s", to, attempt, exc)
        return "not_sent"


emailer = EmailSender()
