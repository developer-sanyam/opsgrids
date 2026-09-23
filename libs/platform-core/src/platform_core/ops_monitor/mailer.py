"""Alert delivery (M3) — direct SMTP, deliberately not through notification-service.

notification-service is one of the things this monitor watches (its outbox, its readiness).
Routing an alert about the platform being unwell through a piece of the platform that might be
the unwell part is the failure mode this module exists to avoid — so it speaks `smtplib`
straight to the box's own `SMTP_*` settings, off the event loop via `asyncio.to_thread` (stdlib
`smtplib` is blocking; there is no async client in the platform's dependencies and this module
adds none).

If SMTP is not configured, nothing is sent — that is `OpsMonitorSettings.smtp_configured`,
checked once per send rather than trusted to have been checked already, so a caller that
forgets still gets the loud, correct outcome: `ops.alert.firing` logged at error, and the
caller's own `/status` shows `email: "disabled — SMTP_HOST/OPS_ALERT_EMAIL/EMAIL_FROM not set"`.
"""

from __future__ import annotations

import asyncio
import smtplib
from datetime import UTC, datetime
from email.message import EmailMessage

from platform_core.logging import get_logger
from platform_core.ops_monitor.config import OpsMonitorSettings

log = get_logger("ops_monitor")


class Mailer:
    def __init__(self, settings: OpsMonitorSettings) -> None:
        self._settings = settings
        self.last_send_at: str | None = None
        self.last_error: str | None = None

    @property
    def configured(self) -> bool:
        return self._settings.smtp_configured

    def status(self) -> dict[str, str | None]:
        if not self.configured:
            return {
                "email": "disabled — SMTP_HOST/OPS_ALERT_EMAIL/EMAIL_FROM not set",
                "last_send_at": self.last_send_at,
                "last_error": self.last_error,
            }
        return {
            "email": f"enabled — alerts to {self._settings.ops_alert_email}",
            "last_send_at": self.last_send_at,
            "last_error": self.last_error,
        }

    async def send(self, subject: str, body: str) -> bool:
        """Send one email; `True` on success. Never raises — a failed alert must not crash
        the monitor sending it."""
        if not self.configured:
            log.error("ops.alert.firing", reason="SMTP not configured", subject=subject)
            self.last_error = "SMTP_HOST/OPS_ALERT_EMAIL/EMAIL_FROM not set"
            return False
        try:
            await asyncio.to_thread(self._send_sync, subject, body)
        except Exception as exc:  # reported, not raised: a bad send must not crash the loop
            log.error("ops.alert.send_failed", subject=subject, error=str(exc)[:300])
            self.last_error = str(exc)[:300]
            return False
        self.last_send_at = datetime.now(UTC).isoformat()
        self.last_error = None
        return True

    def _send_sync(self, subject: str, body: str) -> None:
        settings = self._settings
        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = settings.email_from
        message["To"] = settings.ops_alert_email
        message.set_content(body)

        with smtplib.SMTP(settings.smtp_host or "", settings.smtp_port, timeout=10) as smtp:
            if settings.smtp_use_tls:
                smtp.starttls()
            if settings.smtp_username:
                smtp.login(settings.smtp_username, settings.smtp_password or "")
            smtp.send_message(message)
