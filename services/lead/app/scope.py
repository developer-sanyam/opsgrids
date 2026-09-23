"""How the `crm` product turns a scope rung into a query filter, on the lead intake side.

The scope ladder (`OWN < TEAM < LOCATION < ORG`) is universal, but what each rung *means* is
per product (ADR-0000 §6). Leads narrow by the rep working them, same as the account book and
the pipeline:

    OWN   -> the leads a rep owns (their rep id is `subject_ref`)
    ORG   -> every lead in the organization

TEAM and LOCATION are HR-shaped ideas that do not map onto a sales book, so this product does
not offer them here either — a selector passes only `owner_col`, and a caller granted TEAM or
LOCATION scope on a lead is refused by `ScopeContext.apply` rather than silently widened.

Lead-service is the third process registering the `crm` product (ADR-0017), alongside
customer-service (the account book) and deal-service (the pipeline) — each owns the resolution
for the rows it serves, exactly as the HRMS services do.
"""

from __future__ import annotations

from typing import Any

from app.models import Lead
from platform_core.auth import register_scope_resolver

PRODUCT = "crm"


class LeadScopeResolver:
    """The `ScopeResolver` for leads. OWN is the owning rep; TEAM/LOCATION are unsupported."""

    def owner_column(self) -> Any:
        return Lead.owner_id

    def team_column(self) -> Any | None:
        return None

    def location_column(self) -> Any | None:
        return None


def register() -> None:
    """Register the resolver for the `crm` product. Idempotent — safe to call at import."""
    register_scope_resolver(PRODUCT, LeadScopeResolver)
