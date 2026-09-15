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


def settings() -> Settings:
    return settings_for(Settings)  # type: ignore[return-value]
