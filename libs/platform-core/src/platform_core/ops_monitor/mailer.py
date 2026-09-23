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
import ssl
from datetime import UTC, datetime
from email.message import EmailMessage

from platform_core.logging import get_logger
from platform_core.ops_monitor.config import OpsMonitorSettings
from platform_core.ops_monitor.redact import summarize_exception

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
        """Never the actual `OPS_ALERT_EMAIL` address (round 2) — `/status` is reachable,
        unauthenticated, to anything already on `back`, a much larger audience than this box's
        own `.env`. Whether alerts are configured at all, and whether the last one landed, is
        all that endpoint needs to say."""
        if not self.configured:
            return {
                "email": "disabled — SMTP_HOST/OPS_ALERT_EMAIL/EMAIL_FROM not set",
                "last_send_at": self.last_send_at,
                "last_error": self.last_error,
            }
        return {
            "email": "enabled",
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
            # last_error surfaces on /status (round 2's redaction) — a summary, never the raw
            # message, which could carry the SMTP host or another operational detail.
            self.last_error = summarize_exception(exc)
            return False
        self.last_send_at = datetime.now(UTC).isoformat()
        self.last_error = None
        return True

    def _send_sync(self, subject: str, body: str) -> None:
        """M5: a verified TLS context on `starttls`, implicit TLS (`SMTP_SSL`) as an
        alternative to it, and a hard refusal to `login()` — send a password anywhere — over a
        connection that ended up neither. A default `starttls()` with no `context=` accepts
        whatever the interpreter's own default happens to be, which has not always meant
        "verified"; passing `ssl.create_default_context()` explicitly means this always checks
        the hostname and the chain, on every Python this runs on.
        """
        settings = self._settings
        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = settings.email_from
        message["To"] = settings.ops_alert_email
        message.set_content(body)

        if settings.smtp_use_tls and settings.smtp_use_ssl:
            raise ValueError(
                "SMTP_USE_TLS and SMTP_USE_SSL are mutually exclusive — STARTTLS upgrades a "
                "plain connection, SSL/SMTPS is encrypted from the first byte; pick one"
            )

        context = ssl.create_default_context()
        host = settings.smtp_host or ""
        smtp: smtplib.SMTP
        if settings.smtp_use_ssl:
            smtp = smtplib.SMTP_SSL(host, settings.smtp_port, timeout=10, context=context)
        else:
            smtp = smtplib.SMTP(host, settings.smtp_port, timeout=10)

        with smtp:
            if settings.smtp_use_tls:
                smtp.starttls(context=context)
            encrypted = settings.smtp_use_tls or settings.smtp_use_ssl
            if settings.smtp_username:
                if not encrypted:
                    raise ValueError(
                        "refusing to send SMTP_USERNAME/SMTP_PASSWORD over an unencrypted "
                        "connection — set SMTP_USE_TLS or SMTP_USE_SSL"
                    )
                smtp.login(settings.smtp_username, settings.smtp_password or "")
            smtp.send_message(message)
