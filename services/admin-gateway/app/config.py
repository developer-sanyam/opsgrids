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

    def model_post_init(self, _: object) -> None:
        """This gateway's policy signs every proxied request with the admin hop signature
        (`GatewayPolicy.sign_admin_hop=True`, `main.py`), and auth refuses an operator request
        that does not carry a valid one. A signing gateway with no key to sign with would
        forward unsigned requests that auth then rejects — every operator sign-in a 401 with no
        hint why — so in a real deployment it refuses to start instead (NIT 8 of the final
        review). In development an unset key stays a no-op on both sides."""
        super().model_post_init(_)
        if (
            self.environment in ("production", "staging", "uat")
            and self.admin_gateway_hop_key is None
        ):
            raise ValueError(
                "admin_gateway_hop_key must be set in production, staging and uat: this gateway "
                "signs every operator request with it, and auth refuses unsigned ones."
            )


def settings() -> Settings:
    return settings_for(Settings)  # type: ignore[return-value]
