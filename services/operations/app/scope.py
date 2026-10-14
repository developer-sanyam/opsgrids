"""How the `operations` product turns a scope rung into a query filter.

The scope ladder (`OWN < TEAM < LOCATION < ORG`) is universal, but what each rung *means* is per
product (ADR-0000 §6). Operations is an org-wide procurement and inventory tool, so its natural
readings are:

    OWN   -> the purchase orders a caller raised (their directory id is `raised_by_id`)
    ORG   -> everything in the organization

TEAM and LOCATION are HR-shaped ideas that do not map onto procurement, so this product does not
offer them: a selector passes only `owner_col`, and a caller granted TEAM or LOCATION scope on an
operations resource is refused by `ScopeContext.apply` rather than silently widened.

Suppliers, inventory and warehouses have no per-person owner — they are org reference data — so
their selectors do not narrow by scope at all (read still requires the permission). The purchase
order is the resource with a meaningful OWN reading, and it is the representative the resolver
names; every selector still passes its own owner column to `ctx.apply`, the enforcement point.
"""

from __future__ import annotations

from app.models import PurchaseOrder
from platform_core.auth import column_scope_resolver, register_scope_resolver

PRODUCT = "operations"


def register() -> None:
    """Register the resolver for the `operations` product. Idempotent — safe to call at import."""
    register_scope_resolver(PRODUCT, column_scope_resolver(owner=PurchaseOrder.raised_by_id))
