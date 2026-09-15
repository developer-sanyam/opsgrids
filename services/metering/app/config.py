"""Configuration for the metering service."""

from __future__ import annotations

from platform_core.config import ServiceSettings, settings_for


class Settings(ServiceSettings):
    service_name: str = "metering"
    db_schema: str = "metering"
    #: Its own NOBYPASSRLS role, granted DML on the `metering` schema and nothing else — so this
    #: service cannot read another product's tables, the whole point of the split. It is also the
    #: role the append-only REVOKE targets: `usage_events` has UPDATE/DELETE taken away from it at
    #: the database, so no code path this role can reach may rewrite a recorded meter reading.
    db_app_role: str | None = "opsgrids_metering"
    db_admin_role: str | None = "opsgrids_admin"
    #: The outbox relay's role: BYPASSRLS, SELECT+UPDATE on `outbox` only. Kept from the scaffold —
    #: metering emits `commerce.usage.recorded`.
    db_outbox_role: str | None = "opsgrids_outbox"


def settings() -> Settings:
    return settings_for(Settings)  # type: ignore[return-value]
