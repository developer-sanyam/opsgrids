"""How the `pulseboard` product turns a scope rung into a query filter, on this service.

The scope ladder (`OWN < TEAM < LOCATION < ORG`) is universal, but what each rung *means* is
per product (ADR-0000 §6). A delivery board is an org-wide operational tool, so its natural
readings are:

    OWN   -> the engagements a caller owns / the board cards assigned to them (their person
             id is `subject_ref`)
    ORG   -> the whole portfolio

TEAM and LOCATION are HR-shaped ideas that do not map onto a delivery board, so this product
does not offer them: a selector passes only `owner_col`, and a caller granted TEAM or LOCATION
scope on a pulseboard resource is refused by `ScopeContext.apply` rather than silently widened.

The registered resolver names the engagement resource as the representative; every selector
still passes its own owner column (`Project.owner_id`, `Task.assignee_id`) to `ctx.apply`,
which is the actual enforcement point.
"""

from __future__ import annotations

from typing import Any

from app.models import Project
from platform_core.auth import register_scope_resolver

PRODUCT = "pulseboard"


class ProjectScopeResolver:
    """The `ScopeResolver` for engagement rows. OWN is the owner; TEAM/LOCATION unsupported."""

    def owner_column(self) -> Any:
        return Project.owner_id

    def team_column(self) -> Any | None:
        return None

    def location_column(self) -> Any | None:
        return None


def register() -> None:
    """Register the resolver for the `pulseboard` product. Idempotent — safe at import."""
    register_scope_resolver(PRODUCT, ProjectScopeResolver)
