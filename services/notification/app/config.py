"""Configuration for the notification service.

Transport settings are environment, never repository data. The default transport is `log`,
which renders and records a message and sends nothing: a service started without credentials
must be inert rather than half-configured, and a misconfigured production deploy should fail
its own assertion rather than quietly drop a customer's invoice reminder.
"""

from __future__ import annotations

from pydantic import SecretStr

from platform_core.config import ServiceSettings, settings_for
from platform_core.smtp_tls import UNVERIFIED_TLS_ENVIRONMENTS, TlsMode


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
    #: STARTTLS on the plain port (587 by convention). The platform's own SMTP credential is only
    #: ever sent after this upgrade, and the server's certificate is verified — see
    #: `smtp_allow_unverified_tls` for the one exception.
    smtp_use_tls: bool = False
    #: Implicit TLS from the first byte ("SMTPS", 465 by convention) instead of STARTTLS. Mutually
    #: exclusive with `smtp_use_tls`. Neither set means an unencrypted connection, which is refused
    #: whenever a username and password are configured (`transports.SmtpTransport`).
    smtp_use_ssl: bool = False
    #: Accept a certificate that does not verify (self-signed, wrong name) on SMTP TLS — for a
    #: development or CI mail server only. **Refused at startup in production, staging and uat**
    #: (`model_post_init`): a deployed service always checks who it is sending credentials to.
    #: It applies to every SMTP connection this process opens, a customer's own included, because
    #: the environment it is allowed in is the one where nothing real is being sent.
    smtp_allow_unverified_tls: bool = False
    smtp_timeout_seconds: float = 10.0
    email_from: str = "no-reply@opsgrids.example.com"

    #: Its own SELECT-only BYPASSRLS reader, for the one lookup that happens before a tenant
    #: is known: Meta posts every organization's webhooks to one URL, and the payload's
    #: `phone_number_id` is what says whose they are. Same shape as account's and console's
    #: directory readers, and confined to `provider_accounts` alone.
    db_directory_role: str | None = "opsgrids_notification_directory"

    # ----------------------------------------------------------------- inbound webhooks
    #: Meta's App Secret, used to verify `X-Hub-Signature-256` over the raw request body.
    #:
    #: Platform configuration rather than per-tenant, because the App is the vendor's: tenants
    #: onboard their WhatsApp Business Account to it, so one secret signs every tenant's
    #: webhooks. A tenant's own credential is its access token, which *is* per-account and
    #: sealed in `provider_accounts`.
    whatsapp_app_secret: SecretStr | None = None
    #: The token echoed back during Meta's `GET` verification handshake. Also app-level.
    whatsapp_verify_token: SecretStr | None = None

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

    #: Where to ask whether a membership id an assign call named actually belongs to the caller's
    #: organization and is active — account-service owns the roster, and a service that took the
    #: id on faith would let a conversation be "assigned" to someone from a different tenant, or
    #: to nobody at all.
    account_internal_url: str = "http://localhost:8111"
    account_timeout_seconds: float = 5.0

    # ----------------------------------------------------------------- engage sending identity
    #: Test sends per organization per minute. Independent of `daily_cap`, which is the
    #: provider's own limit on a *connected* connection — this bounds the route itself, which
    #: takes an arbitrary `to_address` and opens a real SMTP handshake on every call.
    engage_test_send_rate_limit_per_minute: int = 5

    # ----------------------------------------------------------------- engage audience
    #: Signs the unsubscribe links in Engage mail (HMAC-SHA256 over organization + address).
    #:
    #: Platform configuration, never per-organization: the link is verified before any tenant is
    #: known, so the key cannot live in a tenant's rows. The default is a `dev-only-` placeholder;
    #: a real environment sets `ENGAGE_UNSUBSCRIBE_SECRET`, and `app.domain.unsubscribe_key`
    #: refuses the placeholder outside local/test so a forgeable key can never sign a link there.
    #: Rotating it invalidates every link already sent — rotate only with that accepted.
    engage_unsubscribe_secret: SecretStr = SecretStr("dev-only-engage-unsubscribe-secret")

    #: Where the unsubscribe link in campaign mail points: the public API base the gateway serves
    #: `/engage/unsubscribe/{token}` under. The link is the one URL a recipient must be able to
    #: open without signing in, so it is configuration rather than derived from a request.
    engage_public_api_base: str = "http://localhost:8080/api/v1"

    # ----------------------------------------------------------------- engage campaigns
    #: Recipients expanded per transaction when a send is requested, and claimed per batch by
    #: the send driver (`app/cron.py`) afterwards.
    engage_campaign_batch_size: int = 200
    #: How often the send driver polls for campaigns to work. Not a busy loop: a tick that finds
    #: nothing to do just sleeps this long before the next one.
    engage_campaign_poll_interval_seconds: float = 15.0
    #: How long a claimed batch (`CampaignRecipient.status == 'sending'`) may stay claimed
    #: before another tick treats it as abandoned and reclaims it. Longer than any batch should
    #: plausibly take to send, so a process that is merely slow is never raced with itself.
    engage_campaign_claim_lease_seconds: int = 300
    #: How long a `paused` campaign (no connected provider, no usable sender) sits before the
    #: driver tries it again on its own — the backoff that keeps a still-broken route from being
    #: hammered every poll. An explicit resume (`POST .../resume`) ignores this and retries now.
    engage_campaign_pause_retry_seconds: float = 900.0

    # ----------------------------------------------------------------- dispatcher
    #: Rows claimed per dispatcher pass.
    dispatch_batch_size: int = 50
    #: Seconds between passes when the last one found nothing.
    dispatch_poll_interval: float = 1.0
    #: A message that has failed this many times stops being claimed and waits for an operator.
    dispatch_max_attempts: int = 6

    @property
    def smtp_tls_mode(self) -> TlsMode:
        """How the platform's own SMTP connection is secured."""
        if self.smtp_use_ssl:
            return "ssl"
        return "starttls" if self.smtp_use_tls else "none"

    @property
    def smtp_verify_tls(self) -> bool:
        """Whether SMTP TLS certificates are checked. Always, except when the flag is on **and**
        the environment is one where nothing real is sent — the environment is checked here as
        well as at startup, so the answer never rests on the validator alone."""
        return not (
            self.smtp_allow_unverified_tls and self.environment in UNVERIFIED_TLS_ENVIRONMENTS
        )

    def model_post_init(self, context: object) -> None:
        super().model_post_init(context)
        if self.smtp_use_tls and self.smtp_use_ssl:
            raise ValueError(
                "SMTP_USE_TLS and SMTP_USE_SSL are mutually exclusive — STARTTLS upgrades a "
                "plain connection, SSL/SMTPS is encrypted from the first byte; pick one"
            )
        if self.smtp_allow_unverified_tls and self.environment not in UNVERIFIED_TLS_ENVIRONMENTS:
            raise ValueError(
                f"{self.environment} refuses smtp_allow_unverified_tls: a deployed service must "
                "verify the certificate of every SMTP server it sends credentials to. It exists "
                "for local, test and ci mail servers only."
            )


def settings() -> Settings:
    return settings_for(Settings)  # type: ignore[return-value]
