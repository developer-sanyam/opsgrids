"""How the `support` product turns a scope rung into a query filter.

The scope ladder (`OWN < TEAM < LOCATION < ORG`) is universal, but what each rung *means* is
per product (ADR-0000 §6). Support is a **shared queue**: an agent works every ticket, not only
their own, so the product floors both reading and writing at ORG (see `catalogue.py` and
`api/routes.py`). ORG scope narrows nothing, so in practice `ctx.apply` here is the identity —
the resolver is registered for platform consistency and to give the one narrowing rung that
does exist a column:

    OWN -> Ticket.assignee_ref   (tickets assigned to the caller, were a caller ever OWN-scoped)

TEAM and LOCATION have no support-shaped reading and are left unmapped; a caller who somehow
held one gets a clean "not supported on this resource" rather than a silent hole.
"""

from __future__ import annotations

from typing import Any

from app.models import Ticket
from platform_core.auth import register_scope_resolver

PRODUCT = "support"


class TicketScopeResolver:
    """The `ScopeResolver` for support tickets."""

    def owner_column(self) -> Any:
        return Ticket.assignee_ref

    def team_column(self) -> Any | None:
        return None

    def location_column(self) -> Any | None:
        return None


def register() -> None:
    """Register the resolver for the `support` product. Idempotent — safe to call at import."""
    register_scope_resolver(PRODUCT, TicketScopeResolver)
