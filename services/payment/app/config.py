"""Configuration for the payment service."""

from __future__ import annotations

from pydantic import SecretStr

from platform_core.config import ServiceSettings, settings_for


class Settings(ServiceSettings):
    service_name: str = "payment"
    db_schema: str = "payment"
    #: Its own NOBYPASSRLS role, granted DML on the `payment` schema and nothing else. Every
    #: business table here is tenant-scoped — mandates, charges, refunds and the provider_events
    #: inbox all carry a tenant policy — so the API must connect as a role row-level security can
    #: bind (ADR-0002 finding 2). Append-only on `refunds` and `provider_events` is enforced
    #: *below* this role with a trigger, not by trusting the application to behave.
    db_app_role: str | None = "opsgrids_payment"
    db_admin_role: str | None = "opsgrids_admin"
    #: The outbox relay's role: BYPASSRLS, SELECT+UPDATE on `outbox` only. payment emits
    #: `commerce.payment.captured` / `commerce.payment.failed`, and a failure is what closes the
    #: suspend loop (ADR-0004 §5) — so the relay draining this outbox is load-bearing.
    db_outbox_role: str | None = "opsgrids_outbox"

    # --- provider (Razorpay-first) ------------------------------------------------
    #: The active provider. One provider per deployment for now; the seam (ADR-0004 §6) is built
    #: against Razorpay's constraints so a Stripe adapter fits inside it later without re-shaping.
    payment_provider: str = "razorpay"

    #: Razorpay credentials. **Deliberately absent in development** — there are no live keys, and
    #: the adapter's outbound HTTP is stubbed (deterministic fake provider ids, state driven by
    #: injected webhook fixtures). `None` means "no network is possible", which a test asserts:
    #: the whole charge/mandate/reconcile path runs with these unset. The webhook secret verifies
    #: the HMAC signature Razorpay puts on every callback; in dev a fixed dev-only value stands in
    #: so the signature check is exercised, never skipped.
    razorpay_key_id: str | None = None
    razorpay_key_secret: SecretStr | None = None
    razorpay_webhook_secret: SecretStr = SecretStr("dev-only-razorpay-webhook-secret")

    # --- the stuck-charge sweep (app/cron.py) ---------------------------------------
    #: How long a charge may sit `created` with no `provider_payment_id` before the sweep
    #: resumes it. Comfortably longer than any provider call is expected to take, so a charge
    #: genuinely mid-flight in the request that staged it is never mistaken for stuck.
    charge_reclaim_after_seconds: int = 300
    charge_sweep_poll_interval_seconds: float = 60.0


def settings() -> Settings:
    return settings_for(Settings)  # type: ignore[return-value]
