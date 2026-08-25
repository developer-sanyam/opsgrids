"""How the `pulseboard` product turns a scope rung into a query filter, on this service.

The scope ladder (`OWN < TEAM < LOCATION < ORG`) is universal, but what each rung *means* is
per product (ADR-0000 §6). A timesheet's natural readings are:

    OWN   -> the caller's own weeks (their roster-person id is `subject_ref`, and every
             timesheet carries that same UUID as `person_id`)
    ORG   -> the whole team's grid

TEAM and LOCATION are HR-shaped ideas that do not map onto a delivery-time grid, so this
product does not offer them: a selector passes only `owner_col`, and a caller granted TEAM or
LOCATION scope on a pulseboard resource is refused by `ScopeContext.apply` rather than
silently widened.
"""

from __future__ import annotations

from typing import Any

from app.models import Timesheet
from platform_core.auth import register_scope_resolver

PRODUCT = "pulseboard"


class TimesheetScopeResolver:
    """The `ScopeResolver` for timesheet rows. OWN is the person; TEAM/LOCATION unsupported."""

    def owner_column(self) -> Any:
        return Timesheet.person_id

    def team_column(self) -> Any | None:
        return None

    def location_column(self) -> Any | None:
        return None


def register() -> None:
    """Register the resolver for the `pulseboard` product. Idempotent — safe at import."""
    register_scope_resolver(PRODUCT, TimesheetScopeResolver)
