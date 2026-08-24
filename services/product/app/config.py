"""Configuration for the product service."""

from __future__ import annotations

from platform_core.config import ServiceSettings, settings_for


class Settings(ServiceSettings):
    service_name: str = "product"
    db_schema: str = "product"
    #: Its own NOBYPASSRLS role, granted DML on the `product` schema and nothing else.
    db_app_role: str | None = "atmora_product"
    db_admin_role: str | None = "atmora_admin"
    #: BYPASSRLS but SELECT-only, on `product.activations` and `product.products`. The internal
    #: entitlements read resolves an organization's activations before any tenant is bound, so
    #: it cannot run on the application role, which reads nothing without a tenant GUC. Its own
    #: role rather than the admin one keeps a bug in that path from writing anything (ADR-0002).
    db_directory_role: str | None = "atmora_product_directory"


def settings() -> Settings:
    return settings_for(Settings)  # type: ignore[return-value]
