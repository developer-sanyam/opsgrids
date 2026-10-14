"""How the `projects` product turns a scope rung into a query filter.

The scope ladder (`OWN < TEAM < LOCATION < ORG`) is universal, but what each rung *means* is
per product (ADR-0000 §6). A delivery portfolio is an org-wide operational tool, so its natural
readings are:

    OWN   -> the projects a caller owns / the tasks assigned to them (their member id is
             `subject_ref`)
    ORG   -> the whole portfolio

TEAM and LOCATION are HR-shaped ideas that do not map onto a delivery board, so this product
does not offer them: a selector passes only `owner_col`, and a caller granted TEAM or LOCATION
scope on a projects resource is refused by `ScopeContext.apply` rather than silently widened.

The registered resolver names the project resource as the representative; every selector still
passes its own owner column (`Project.owner_id`, `Milestone.owner_id`, `Task.assignee_id`) to
`ctx.apply`, which is the actual enforcement point.
"""

from __future__ import annotations

from app.models import Project
from platform_core.auth import column_scope_resolver, register_scope_resolver

PRODUCT = "projects"


def register() -> None:
    """Register the resolver for the `projects` product. Idempotent — safe to call at import."""
    register_scope_resolver(PRODUCT, column_scope_resolver(owner=Project.owner_id))
