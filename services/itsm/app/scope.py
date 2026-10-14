"""How the `itsm` product turns a scope rung into a query filter.

The scope ladder (`OWN < TEAM < LOCATION < ORG`) is universal, but what each rung *means* is
per product (ADR-0000 §6). ITSM is an org-wide operational tool, so its natural readings are:

    OWN   -> the incidents / requests a caller raised (their directory id is `subject_ref`)
    ORG   -> everything in the organization

TEAM and LOCATION are HR-shaped ideas that do not map onto a service desk, so this product does
not offer them: a selector passes only `owner_col`, and a caller granted TEAM or LOCATION scope
on an ITSM resource is refused by `ScopeContext.apply` rather than silently widened.

The registered resolver names the incident resource as the representative; every selector still
passes its own owner column to `ctx.apply`, which is the actual enforcement point.
"""

from __future__ import annotations

from app.models import Incident
from platform_core.auth import column_scope_resolver, register_scope_resolver

PRODUCT = "itsm"


def register() -> None:
    """Register the resolver for the `itsm` product. Idempotent — safe to call at import."""
    register_scope_resolver(PRODUCT, column_scope_resolver(owner=Incident.requester_id))
