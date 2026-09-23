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


def settings() -> Settings:
    return settings_for(Settings)  # type: ignore[return-value]
