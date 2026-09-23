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

    #: Where employee-service answers `GET /internal/v1/employee-links`, the read
    #: `tools/reconcile_membership_links.py` keys on (ADR-0008 §8). Used by that command
    #: only — **no request path in this service calls employee-service**, and ADR-0006 §1
    #: says none ever should.
    employee_service_url: str = "http://localhost:8120"

    #: `POST /domains/{id}/verify` runs a real DNS lookup, so it is bounded per organization
    #: the same way `storefront`'s own domain check is — one caller retrying in a loop must
    #: not become this process's DNS traffic pattern.
    domain_verify_rate_limit_window_seconds: int = 30


def settings() -> Settings:
    return settings_for(Settings)  # type: ignore[return-value]
