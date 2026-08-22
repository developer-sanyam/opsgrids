"""Configuration for the account service."""

from __future__ import annotations

from platform_core.config import ServiceSettings, settings_for


class Settings(ServiceSettings):
    service_name: str = "account"
    db_schema: str = "account"
    db_app_role: str | None = "atmora_account"
    db_admin_role: str | None = "atmora_admin"
    db_directory_role: str | None = "atmora_directory"


def settings() -> Settings:
    return settings_for(Settings)  # type: ignore[return-value]
