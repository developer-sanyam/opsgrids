"""Configuration for the notification service.

Transport settings are environment, never repository data. The default transport is `log`,
which renders and records a message and sends nothing: a service started without credentials
must be inert rather than half-configured, and a misconfigured production deploy should fail
its own assertion rather than quietly drop a customer's invoice reminder.
"""

from __future__ import annotations

from pydantic import SecretStr, model_validator

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

    # ----------------------------------------------------------------- WhatsApp Embedded Signup
    #: The vendor's Meta app, for the Embedded Signup flow (slice W1). All optional at boot: with
    #: `META_APP_ID`, `META_APP_SECRET` (or the older `WHATSAPP_APP_SECRET`) or the configuration
    #: id unset, `GET /engage/whatsapp/signup/config` answers `enabled: false` and the manual
    #: connection route keeps working. The secret exchanges the popup's one-time code for a token
    #: and is never sent to a browser.
    meta_app_id: str | None = None
    meta_app_secret: SecretStr | None = None
    meta_embedded_signup_config_id: str | None = None
    #: Overrides the version segment of `whatsapp_api_base` (`v25.0`) for every Graph call. Unset
    #: means "whatever the base already names".
    whatsapp_graph_version: str | None = None

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

    #: Where to fetch a storefront buyer's number for a ticked WhatsApp opt-in
    #: (`app/storefront_optin.py`). Storefront owns the enquiry and audits every read.
    storefront_internal_url: str = "http://localhost:8174"
    storefront_timeout_seconds: float = 5.0

    #: Where to read the leads of a cold-email campaign (`app/crm_leads.py`). Lead owns the
    #: address and the key; every read is audited there.
    lead_internal_url: str = "http://localhost:8135"
    lead_timeout_seconds: float = 5.0

    # ----------------------------------------------------------------- engage sending identity
    #: Cold e-mail to leads (ADR-0038 addendum 2026-10-16): the most one organization sends in a
    #: UTC day across its `cold_outreach` campaigns, and the most leads one campaign may target.
    #: The rest of a larger audience stays queued and goes out on the following days.
    engage_cold_daily_cap: int = 200
    engage_cold_audience_max: int = 2000
    #: Audience previews and drafts per person per minute: each reads leads' addresses from lead.
    engage_lead_audience_rate_limit_per_minute: int = 30
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

    #: Optional key for the signed open/click tracking tokens (`app/e1_tracking.py`). Empty means
    #: "derive it from `engage_unsubscribe_secret`", so tracking needs no new key at boot and is
    #: off wherever the unsubscribe key is refused. Rotating it stops recording opens and clicks
    #: on mail already sent.
    engage_tracking_secret: SecretStr = SecretStr("")

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
    #: A failed send is only retried while the message is younger than this (measured from when it
    #: was staged). Past it, a still-`pending` message that has failed before is failed
    #: permanently as `retry_window_expired` instead of being sent hours or days late. Optional:
    #: `DISPATCH_RETRY_MAX_AGE_SECONDS`, default 6 hours.
    dispatch_retry_max_age_seconds: int = 21600

    #: Runs this service's worker loops — the two event consumers and the campaign send driver
    #: (`app/worker.py`) — as supervised background tasks inside the API process instead of the
    #: separate `notification-worker` container (the memory-headroom follow-up that folds every
    #: service's remaining worker in). Off by default everywhere except the edge box's own
    #: `.env`: `bin/worker` still runs `app.worker` standalone unchanged, and setting this to
    #: `false` while adding `split-workers` to `EDGE_PROFILES` is the rollback (RUNBOOK.md).
    run_consumers_in_api: bool = False

    @model_validator(mode="after")
    def _unsubscribe_secret_is_real_on_a_real_box(self) -> Settings:
        """Empty env vars fall back to the committed `dev-only-` default, which would let anyone
        who has read the repo forge unsubscribe and tracking tokens. Refused outside development."""
        if self.environment in ("production", "staging", "uat") and (
            self.engage_unsubscribe_secret.get_secret_value().startswith(("dev-only-", "ci-only-"))
        ):
            raise ValueError(
                f"engage_unsubscribe_secret must be a real secret in {self.environment}"
            )
        return self

    @model_validator(mode="after")
    def _apply_graph_version(self) -> Settings:
        """`WHATSAPP_GRAPH_VERSION` replaces the version segment of `whatsapp_api_base`, so every
        Graph caller (sends, template sync, signup) moves with one config change."""
        version = (self.whatsapp_graph_version or "").strip().strip("/")
        if version:
            base = self.whatsapp_api_base.rstrip("/")
            head, _, _tail = base.rpartition("/")
            self.whatsapp_api_base = (
                f"{head}/{version}" if head.count("/") >= 2 else f"{base}/{version}"
            )
        return self


def graph_version(cfg: Settings) -> str:
    """The Graph API version segment every WhatsApp call uses (`v25.0`)."""
    return cfg.whatsapp_api_base.rstrip("/").rsplit("/", 1)[-1]


def meta_app_secret(cfg: Settings) -> str | None:
    """The Meta app secret, or the older WhatsApp one."""  # pragma: allowlist secret
    secret = cfg.meta_app_secret or cfg.whatsapp_app_secret
    return secret.get_secret_value() if secret is not None else None


def settings() -> Settings:
    return settings_for(Settings)  # type: ignore[return-value]
