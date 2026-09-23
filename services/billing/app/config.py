"""Configuration for the billing service."""

from __future__ import annotations

from platform_core.config import ServiceSettings, settings_for


class Settings(ServiceSettings):
    service_name: str = "billing"
    db_schema: str = "billing"
    #: Its own NOBYPASSRLS role, granted DML on the `billing` schema and nothing else. Billing
    #: is a tenant service — `invoices`, `invoice_lines`, `credit_ledger`, `audit_logs` and
    #: `outbox` all carry a tenant policy — so the API must connect as a role row-level security
    #: can bind (ADR-0002 finding 2). Append-only and freeze are enforced *below* this role with
    #: table privileges and a trigger, not by trusting the application to behave.
    db_app_role: str | None = "opsgrids_billing"
    db_admin_role: str | None = "opsgrids_admin"
    #: The outbox relay's role: BYPASSRLS, SELECT+UPDATE on `outbox` only. Kept from the
    #: scaffold — billing emits `commerce.invoice.*` and `commerce.credit.*`.
    db_outbox_role: str | None = "opsgrids_outbox"

    # --- the billing run (ADR-0005 addendum, 2026-09-23) -----------------------------------
    #: Where catalog answers the plan-version read. The billing run prices a subscription period
    #: off the **pinned** version's own prices, read here — never off a price the caller sent.
    catalog_service_url: str = "http://localhost:8164"
    #: Where metering answers the per-period usage read, for a version that prices a meter.
    metering_service_url: str = "http://localhost:8162"

    #: Which `tax_rates` row applies to a billing-run invoice. Jurisdiction resolution per
    #: customer is still open (ADR-0004 "Open: Tax"), so until it is settled this is one
    #: deployment-wide choice: a `code` (`gst`) and optional `jurisdiction` (`IN`). Unset means
    #: **no tax line** — the honest state for a deployment that has not registered for tax. Set,
    #: the rate in force on the period's start date is applied, and a period with no rate in force
    #: is refused rather than invoiced untaxed.
    billing_tax_code: str | None = None
    billing_tax_jurisdiction: str | None = None


def settings() -> Settings:
    return settings_for(Settings)  # type: ignore[return-value]
