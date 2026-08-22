"""Configuration for the product service."""

from __future__ import annotations

from platform_core.config import ServiceSettings, settings_for


class Settings(ServiceSettings):
    service_name: str = "product"
    db_schema: str = "product"
    #: Its own NOBYPASSRLS role, granted DML on the `product` schema and nothing else.
    db_app_role: str | None = "atmora_product"
    db_admin_role: str | None = "atmora_admin"


def settings() -> Settings:
    return settings_for(Settings)  # type: ignore[return-value]
