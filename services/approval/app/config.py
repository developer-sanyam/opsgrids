"""Configuration for the approval service.

A platform service (ADR-0006 §1): no `product` key in the gateway route table, port 8152 in the
platform band (ADR-0006 §14). Every table it owns is tenant-scoped, so the API connects as its
own NOBYPASSRLS role — the same discipline every other service in the estate follows.
"""

from __future__ import annotations

from platform_core.config import ServiceSettings, settings_for


class Settings(ServiceSettings):
    service_name: str = "approval"
    db_schema: str = "approval"
    #: Its own NOBYPASSRLS role, granted DML on the `approval` schema and nothing else.
    db_app_role: str | None = "opsgrids_approval"
    db_admin_role: str | None = "opsgrids_admin"
    #: The outbox relay's role: BYPASSRLS, SELECT+UPDATE on `outbox` only.
    db_outbox_role: str | None = "opsgrids_outbox"

    #: Where employee-service answers the internal hierarchy backfill (ADR-0006 §2.1). Service-
    #: token authenticated, never reachable from outside the cluster.
    employee_service_url: str = "http://localhost:8120"


def settings() -> Settings:
    return settings_for(Settings)  # type: ignore[return-value]
