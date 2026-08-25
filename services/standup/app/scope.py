"""How the `pulseboard` product turns a scope rung into a query filter, on this service.

The scope ladder (`OWN < TEAM < LOCATION < ORG`) is universal, but what each rung *means* is
per product (ADR-0000 §6). A standup wall's natural readings are:

    OWN   -> the caller's own check-ins (their roster-person id is `subject_ref`, and every
             entry carries that same UUID as `person_id`)
    ORG   -> the whole team's wall

TEAM and LOCATION are HR-shaped ideas that do not map onto a standup wall, so this product
does not offer them: a selector passes only `owner_col`, and a caller granted TEAM or LOCATION
scope on a pulseboard resource is refused by `ScopeContext.apply` rather than silently widened.
"""

from __future__ import annotations

from typing import Any

from app.models import StandupEntry
from platform_core.auth import register_scope_resolver

PRODUCT = "pulseboard"


class StandupScopeResolver:
    """The `ScopeResolver` for standup rows. OWN is the person; TEAM/LOCATION unsupported."""

    def owner_column(self) -> Any:
        return StandupEntry.person_id

    def team_column(self) -> Any | None:
        return None

    def location_column(self) -> Any | None:
        return None


def register() -> None:
    """Register the resolver for the `pulseboard` product. Idempotent — safe at import."""
    register_scope_resolver(PRODUCT, StandupScopeResolver)
