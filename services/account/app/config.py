"""Configuration for the account service."""

from __future__ import annotations

from platform_core.config import ServiceSettings, settings_for


class Settings(ServiceSettings):
    service_name: str = "account"
    db_schema: str = "account"
    db_app_role: str | None = "opsgrids_account"
    db_admin_role: str | None = "opsgrids_admin"
    db_outbox_role: str | None = "opsgrids_outbox"
    db_directory_role: str | None = "opsgrids_directory"

    #: Where the product permission manifests live (`<product>.json`, written by
    #: `tools/export_permissions.py`). Unset means "walk up to the repository's
    #: `libs/contracts/permissions/`", which is the development and CI layout; a packaged
    #: deployment ships the manifests and points this at them.
    permission_manifest_dir: str | None = None


def settings() -> Settings:
    return settings_for(Settings)  # type: ignore[return-value]
