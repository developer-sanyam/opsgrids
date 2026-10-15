"""Configuration for the lead service."""

from __future__ import annotations

from platform_core.config import ServiceSettings, settings_for


class Settings(ServiceSettings):
    service_name: str = "lead"
    db_schema: str = "lead"
    #: Its own NOBYPASSRLS role, granted DML on the `lead` schema and nothing else — so
    #: this service cannot read another product's tables, the whole point of the split.
    db_app_role: str | None = "opsgrids_lead"
    db_admin_role: str | None = "opsgrids_admin"
    db_outbox_role: str | None = "opsgrids_outbox"

    # ----------------------------------------------------------------- conversion targets
    #: Where `:convert` asks for the company (+ contact) a lead becomes. Lead never touches
    #: `customer`'s schema — it asks over the internal API under a token addressed to
    #: customer by name, the same shape notification already uses to resolve a contact.
    customer_internal_url: str = "http://localhost:8130"
    customer_timeout_seconds: float = 5.0
    #: Where `:convert` asks for the deal a lead becomes. Same shape, addressed to deal.
    deal_internal_url: str = "http://localhost:8131"
    deal_timeout_seconds: float = 5.0

    # ----------------------------------------------------------------- reveal budget
    #: How many real phone numbers / emails one user may reveal in one organization
    #: (`services.enforce_reveal_budget`): `reveal_burst_limit` per `reveal_burst_window_seconds`
    #: and `reveal_daily_limit` per UTC day. With no step-up in front of a reveal (ADR-0023
    #: addendum),
    #: without this a single session could open every lead the gateway's own 300
    #: requests a minute allowed. The budget is shared with customer-service's contact reveal
    #: (same Redis keys): it bounds how much PII one person can read, not how often one route
    #: is called. Raise it only with a reason.
    #: Sized for a rep working a call list (owner decision 2026-10-15, ADR-0023 addendum): about 200
    #: records a day, each with a phone and possibly an email, in bursts of ~30 records in ten
    #: minutes, with headroom for contact and lead reveals sharing the counters. It still stops a
    #: scrape: 60 per ten minutes and 600 per day is nowhere near a bulk export, and every reveal
    #: writes an audit row. The Redis key names are unchanged; only these limits moved (they are
    #: script arguments, not part of the key).
    reveal_burst_limit: int = 60
    reveal_burst_window_seconds: int = 600
    reveal_daily_limit: int = 600


def settings() -> Settings:
    return settings_for(Settings)  # type: ignore[return-value]
