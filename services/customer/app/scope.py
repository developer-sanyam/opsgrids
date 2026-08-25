"""How the `crm` product turns a scope rung into a query filter, on the account book side.

The scope ladder (`OWN < TEAM < LOCATION < ORG`) is universal, but what each rung *means*
is per product (ADR-0000 §6). CRM's natural narrowing is account ownership, so its
readings are:

    OWN   -> the companies / contacts a rep owns (their rep id is `subject_ref`)
    ORG   -> the whole book

TEAM and LOCATION are HR-shaped ideas that do not map onto a sales book — territory would
be the CRM analogue and is not modelled in this slice — so this product does not offer
them: a selector passes only `owner_col`, and a caller granted TEAM or LOCATION scope on a
CRM resource is refused by `ScopeContext.apply` rather than silently widened.

The registered resolver names the company resource as the representative; every selector
still passes its own owner column to `ctx.apply`, which is the actual enforcement point.
Deal-service registers the same `crm` product in its own process for the pipeline side —
each process owns the resolution for the rows it serves, exactly as the HRMS services do.
"""

from __future__ import annotations

from typing import Any

from app.models import Company
from platform_core.auth import register_scope_resolver

PRODUCT = "crm"


class CompanyScopeResolver:
    """The `ScopeResolver` for the account book. OWN is the owning rep; TEAM/LOCATION are
    unsupported."""

    def owner_column(self) -> Any:
        return Company.owner_id

    def team_column(self) -> Any | None:
        return None

    def location_column(self) -> Any | None:
        return None


def register() -> None:
    """Register the resolver for the `crm` product. Idempotent — safe to call at import."""
    register_scope_resolver(PRODUCT, CompanyScopeResolver)
