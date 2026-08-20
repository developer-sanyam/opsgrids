"""Configuration for the operator gateway.

Owns no tables; `db_schema` exists only to satisfy the base settings.
"""

from __future__ import annotations

from platform_core.config import ServiceSettings, settings_for


class Settings(ServiceSettings):
    service_name: str = "admin-gateway"
    db_schema: str = "public"

    routes_file: str = "routes.json"

    #: A distinct audience from the product gateway. A token good for the customer's own
    #: application is **not** good here, so a stolen product token cannot reach the
    #: operator surface even if the network path exists.
    audience: str = "operator"

    account_service_url: str = "http://localhost:8111"

    #: Lower than the product gateway. This surface is used by a handful of staff and a
    #: few partner integrations; a spike here is a signal, not traffic.
    rate_limit_per_minute: int = 120
    anonymous_rate_limit_per_minute: int = 30
    upstream_timeout_seconds: float = 60.0


def settings() -> Settings:
    return settings_for(Settings)  # type: ignore[return-value]
