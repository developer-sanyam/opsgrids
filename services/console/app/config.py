"""Configuration for the console service.

The operator console reads *across* tenants — it is the vendor looking at every customer at
once — so unlike a product service it carries a directory role as well as an app role
(ADR-0003 §2, ADR-0002 §2). The projections it owns are tenant-scoped and RLS-policied; the
consumer writes them one organization at a time as the NOBYPASSRLS app role with a per-message
GUC, and the staff read API reads them all at once on the SELECT-only BYPASSRLS directory role.
"""

from __future__ import annotations

from platform_core.config import ServiceSettings, settings_for


class Settings(ServiceSettings):
    service_name: str = "console"
    db_schema: str = "console"
    #: Its own NOBYPASSRLS role, granted DML on the `console` schema and nothing else. The
    #: consumer writes the projections as this role, one tenant at a time (GUC per message), so
    #: the RLS `WITH CHECK` on each projection binds exactly as it does for a customer write.
    db_app_role: str | None = "opsgrids_console"
    db_admin_role: str | None = "opsgrids_admin"
    #: BYPASSRLS but SELECT-only, on the projection tables. The staff read API resolves every
    #: organization's commercial state at once — there is no single tenant to bind — so it cannot
    #: run on the application role, which reads nothing without a tenant GUC. Its own role rather
    #: than the admin one keeps a bug in that read path from writing anything (ADR-0002 §2).
    db_directory_role: str | None = "opsgrids_console_directory"
    #: The outbox relay's role: BYPASSRLS, SELECT+UPDATE on `outbox` only. Kept from the scaffold
    #: so the standard `/health/outbox` probe is honest even though console currently publishes
    #: nothing — it is a pure projection consumer.
    db_outbox_role: str | None = "opsgrids_outbox"

    #: Where the owning services expose the internal endpoints an operator override delegates to.
    #: Console never writes another service's schema (ADR-0003 §2): a suspend calls subscription's
    #: `/internal/v1/subscriptions/{id}/suspend`, a comp grant calls billing's `/internal/v1/credit`
    #: — each under a short-lived service token addressed to that service by name. These are the
    #: only coupling to those services besides the event routing keys, and it is over HTTP, never
    #: an import (mirrors the port map in the commerce plan: subscription 8161, billing 8160).
    subscription_internal_url: str = "http://localhost:8161"
    billing_internal_url: str = "http://localhost:8160"
    #: Where the pinned price for a subscription's plan version is resolved from — catalog's
    #: internal read `GET /internal/v1/plan-versions/{id}`. MRR is active subs times this price
    #: (ADR-0004 §4). Coupled by the documented endpoint shape and a service token, never an import.
    catalog_internal_url: str = "http://localhost:8164"
    #: account-service's own address, for the org-detail composite's directory half
    #: (`app/directory_client.py`). Deliberately the **public** `/api/v1` surface — a staff_only
    #: route the caller's own forwarded token authorizes — never `/internal/v1`, which no
    #: individual caller's permissions gate at all.
    account_public_url: str = "http://localhost:8111"


def settings() -> Settings:
    return settings_for(Settings)  # type: ignore[return-value]
