"""M5: a verified TLS context on STARTTLS, implicit TLS support, and a hard refusal to send
credentials over a connection that ended up neither. Mocked `smtplib` — the real transport is
`test_ops_monitor_integration.py`'s MailHog suite.
"""

from __future__ import annotations

import ssl
from typing import Any, ClassVar

import pytest

from platform_core.ops_monitor.config import OpsMonitorSettings
from platform_core.ops_monitor.mailer import Mailer


class _FakeSMTP:
    """Records every call `_send_sync` makes, standing in for both `smtplib.SMTP` and
    `smtplib.SMTP_SSL` — same public surface, this test only cares which one was constructed
    and with what."""

    instances: ClassVar[list[_FakeSMTP]] = []

    def __init__(self, host: str, port: int, timeout: float = 10, context: Any = None) -> None:
        self.host = host
        self.port = port
        self.init_context = context
        self.starttls_context: Any = "not-called"
        self.login_args: tuple[str, str] | None = None
        self.sent = False
        _FakeSMTP.instances.append(self)

    def starttls(self, context: Any = None) -> None:
        self.starttls_context = context

    def login(self, username: str, password: str) -> None:
        self.login_args = (username, password)

    def send_message(self, message: Any) -> None:
        self.sent = True

    def __enter__(self) -> _FakeSMTP:
        return self

    def __exit__(self, *exc: object) -> None:
        return None


@pytest.fixture(autouse=True)
def _reset_instances() -> None:
    _FakeSMTP.instances = []


def _settings(**overrides: Any) -> OpsMonitorSettings:
    defaults: dict[str, Any] = {
        "smtp_host": "smtp.example.com",
        "smtp_port": 587,
        "email_from": "ops-monitor@example.com",
        "ops_alert_email": "ops@example.com",
    }
    defaults.update(overrides)
    return OpsMonitorSettings(**defaults)


class TestStarttlsUsesAVerifiedContext:
    async def test_starttls_is_called_with_a_verified_default_context(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("smtplib.SMTP", _FakeSMTP)
        mailer = Mailer(_settings(smtp_use_tls=True))

        assert await mailer.send("subject", "body") is True

        smtp = _FakeSMTP.instances[0]
        assert isinstance(smtp.starttls_context, ssl.SSLContext)
        assert smtp.starttls_context.check_hostname is True
        assert smtp.starttls_context.verify_mode is ssl.CERT_REQUIRED

    async def test_no_tls_and_no_username_sends_without_starttls(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("smtplib.SMTP", _FakeSMTP)
        mailer = Mailer(_settings(smtp_use_tls=False))

        assert await mailer.send("subject", "body") is True

        smtp = _FakeSMTP.instances[0]
        assert smtp.starttls_context == "not-called"
        assert smtp.sent is True


class TestImplicitTls:
    async def test_smtp_use_ssl_constructs_smtp_ssl_with_a_verified_context(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("smtplib.SMTP_SSL", _FakeSMTP)
        mailer = Mailer(_settings(smtp_use_ssl=True, smtp_use_tls=False, smtp_port=465))

        assert await mailer.send("subject", "body") is True

        smtp = _FakeSMTP.instances[0]
        assert isinstance(smtp.init_context, ssl.SSLContext)
        assert smtp.port == 465

    async def test_tls_and_ssl_together_is_refused(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("smtplib.SMTP", _FakeSMTP)
        monkeypatch.setattr("smtplib.SMTP_SSL", _FakeSMTP)
        mailer = Mailer(_settings(smtp_use_tls=True, smtp_use_ssl=True))

        assert await mailer.send("subject", "body") is False
        # last_error is a redacted summary (round 2) — the raw "mutually exclusive" message
        # only reaches the process's own log line, never /status; see TestStatusRedaction.
        assert mailer.last_error == "ValueError"


class TestRefusesPlaintextCredentials:
    async def test_login_without_tls_or_ssl_is_refused(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("smtplib.SMTP", _FakeSMTP)
        mailer = Mailer(_settings(smtp_use_tls=False, smtp_username="ops", smtp_password="secret"))

        assert await mailer.send("subject", "body") is False
        # Redacted (round 2) — see TestStatusRedaction for the /status-facing assertion.
        assert mailer.last_error == "ValueError"
        # Never actually attempted — the credential must not reach the wire at all.
        assert _FakeSMTP.instances[0].login_args is None
        assert _FakeSMTP.instances[0].sent is False

    async def test_login_with_starttls_succeeds(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("smtplib.SMTP", _FakeSMTP)
        mailer = Mailer(_settings(smtp_use_tls=True, smtp_username="ops", smtp_password="secret"))

        assert await mailer.send("subject", "body") is True
        assert _FakeSMTP.instances[0].login_args == ("ops", "secret")


class TestStatusRedaction:
    """`/status` is reachable, unauthenticated, to anything already on `back` — a much larger
    audience than this box's own `.env`. Neither the configured alert address nor a raw
    exception message belongs there."""

    def test_status_never_includes_the_configured_alert_email(self) -> None:
        mailer = Mailer(_settings(ops_alert_email="ops@real-company-domain.example"))
        status = mailer.status()
        assert "ops@real-company-domain.example" not in str(status)
        assert status["email"] == "enabled"

    def test_disabled_status_names_no_address_either(self) -> None:
        mailer = Mailer(OpsMonitorSettings())
        status = mailer.status()
        assert "@" not in (status["email"] or "")

    async def test_a_send_failures_last_error_is_a_summary_not_the_raw_message(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class _ExplodingSMTP(_FakeSMTP):
            def __enter__(self) -> _FakeSMTP:
                raise ConnectionRefusedError(
                    "[Errno 61] Connection refused to smtp.internal.example"
                )

        monkeypatch.setattr("smtplib.SMTP", _ExplodingSMTP)
        mailer = Mailer(_settings())

        assert await mailer.send("subject", "body") is False
        assert mailer.last_error is not None
        assert "smtp.internal.example" not in mailer.last_error
        assert "ConnectionRefusedError" in mailer.last_error
