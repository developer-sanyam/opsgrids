"""How the `crm` product turns a scope rung into a query filter, on the pipeline side.

The scope ladder (`OWN < TEAM < LOCATION < ORG`) is universal, but what each rung *means*
is per product (ADR-0000 §6). CRM's natural narrowing is deal ownership, so its readings
are:

    OWN   -> the deals / activities a rep owns (their rep id is `subject_ref`)
    ORG   -> the whole pipeline

TEAM and LOCATION are HR-shaped ideas that do not map onto a pipeline — territory would
be the CRM analogue and is not modelled in this slice — so this product does not offer
them: a selector passes only `owner_col`, and a caller granted TEAM or LOCATION scope on a
CRM resource is refused by `ScopeContext.apply` rather than silently widened.

The registered resolver names the deal resource as the representative; every selector
still passes its own owner column to `ctx.apply`, which is the actual enforcement point.
Customer-service registers the same `crm` product in its own process for the account-book
side — each process owns the resolution for the rows it serves, exactly as the HRMS
services do.
"""

from __future__ import annotations

from typing import Any

from app.models import Deal
from platform_core.auth import register_scope_resolver

PRODUCT = "crm"


class DealScopeResolver:
    """The `ScopeResolver` for pipeline rows. OWN is the owning rep; TEAM/LOCATION are
    unsupported."""

    def owner_column(self) -> Any:
        return Deal.owner_id

    def team_column(self) -> Any | None:
        return None

    def location_column(self) -> Any | None:
        return None


def register() -> None:
    """Register the resolver for the `crm` product. Idempotent — safe to call at import."""
    register_scope_resolver(PRODUCT, DealScopeResolver)
