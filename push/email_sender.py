from __future__ import annotations

import logging
import os
import smtplib
from email.mime.text import MIMEText

logger = logging.getLogger(__name__)


def is_email_explicitly_enabled() -> bool:
    return os.environ.get("ALPHA_EMAIL_ENABLED", "").strip().lower() in ("1", "true", "yes")


def send_email(subject: str, body: str) -> bool:
    """Off by default; set ALPHA_EMAIL_ENABLED=1 and SMTP_* to enable."""

    if not is_email_explicitly_enabled():
        return False

    host = os.environ.get("SMTP_HOST", "").strip()
    user = os.environ.get("SMTP_USER", "").strip()
    password = os.environ.get("SMTP_PASS", "").strip()
    to_addr = os.environ.get("SMTP_TO", "").strip()
    port = int(os.environ.get("SMTP_PORT", "587"))

    if not (host and to_addr):
        logger.warning("email skipped: SMTP_HOST or SMTP_TO missing")
        return False

    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = user or host
    msg["To"] = to_addr

    try:
        with smtplib.SMTP(host, port, timeout=30) as s:
            s.starttls()
            if user and password:
                s.login(user, password)
            s.sendmail(msg["From"], [to_addr], msg.as_string())
        return True
    except Exception:
        logger.exception("email send failed")
        return False
