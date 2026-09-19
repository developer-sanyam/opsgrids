"""Configuration for the notification service.

Transport settings are environment, never repository data. The default transport is `log`,
which renders and records a message and sends nothing: a service started without credentials
must be inert rather than half-configured, and a misconfigured production deploy should fail
its own assertion rather than quietly drop a customer's invoice reminder.
"""

from __future__ import annotations

from pydantic import SecretStr

from platform_core.config import ServiceSettings, settings_for


class Settings(ServiceSettings):
    service_name: str = "notification"
    db_schema: str = "notification"
    #: Its own NOBYPASSRLS role, granted DML on the `notification` schema and nothing else — so
    #: this service cannot read another product's tables, the whole point of the split.
    db_app_role: str | None = "opsgrids_notification"
    db_admin_role: str | None = "opsgrids_admin"
    db_outbox_role: str | None = "opsgrids_outbox"

    # ----------------------------------------------------------------- transports
    #: `log` | `smtp` — which transport serves the email channel.
    email_transport: str = "log"
    smtp_host: str = "localhost"
    #: MailHog, which `make infra` already starts. Not a real relay.
    smtp_port: int = 1025
    smtp_username: str | None = None
    smtp_password: SecretStr | None = None
    smtp_use_tls: bool = False
    smtp_timeout_seconds: float = 10.0
    email_from: str = "no-reply@opsgrids.example.com"

    #: `log` | `whatsapp_cloud` — which transport serves the WhatsApp channel.
    whatsapp_transport: str = "log"
    #: Meta Graph API. The phone number id is the sender, not the sender's number.
    #:
    #: Pinned to an explicit version rather than an alias, because Meta's versions carry
    #: breaking changes and an unpinned call would change behaviour on their release schedule
    #: rather than ours. v25.0 is current (released 2026-02-18); v21.0 was two years behind.
    #: Overridable per environment so a version bump is a config change, not a deploy.
    whatsapp_api_base: str = "https://graph.facebook.com/v25.0"
    whatsapp_phone_number_id: str | None = None
    whatsapp_access_token: SecretStr | None = None
    whatsapp_timeout_seconds: float = 10.0

    # ----------------------------------------------------------------- customer targeting
    #: Where to ask for a contact's messaging address. Notification never decrypts a phone
    #: number itself — customer-service owns the data and the key, and answers over its
    #: internal API under a token addressed to it by name.
    customer_internal_url: str = "http://localhost:8130"
    customer_timeout_seconds: float = 5.0

    # ----------------------------------------------------------------- dispatcher
    #: Rows claimed per dispatcher pass.
    dispatch_batch_size: int = 50
    #: Seconds between passes when the last one found nothing.
    dispatch_poll_interval: float = 1.0
    #: A message that has failed this many times stops being claimed and waits for an operator.
    dispatch_max_attempts: int = 6


def settings() -> Settings:
    return settings_for(Settings)  # type: ignore[return-value]
