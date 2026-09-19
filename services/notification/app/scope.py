"""How the `notification` product turns a scope rung into a query filter.

The scope ladder (`OWN < TEAM < LOCATION < ORG`) is universal, but what each rung *means* is per
product (ADR-0000 §6). Notification is an **organization-wide administrative surface**: templates
and rules govern what the whole organization sends, so every permission tops out at ORG and the
routes floor there. ORG narrows nothing, so `ctx.apply` here is in practice the identity.

The resolver is registered anyway, for platform consistency and to give the one narrowing rung
that could exist a column:

    OWN -> created_by   (rows this principal authored)

TEAM and LOCATION have no notification-shaped reading and are left unmapped; a caller who
somehow held one gets a clean "not supported on this resource" rather than a silent hole.
"""

from __future__ import annotations

from typing import Any

from app.models import Rule
from platform_core.auth import register_scope_resolver

PRODUCT = "notification"


class NotificationScopeResolver:
    """The `ScopeResolver` for notification's administrative rows."""

    def owner_column(self) -> Any:
        return Rule.created_by

    def team_column(self) -> Any | None:
        return None

    def location_column(self) -> Any | None:
        return None


def register() -> None:
    """Register the resolver for the `notification` product. Idempotent — safe at import."""
    register_scope_resolver(PRODUCT, NotificationScopeResolver)
