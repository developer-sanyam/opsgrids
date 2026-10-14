"""How the `marketing` product turns a scope rung into a query filter.

The scope ladder (`OWN < TEAM < LOCATION < ORG`) is universal, but what each rung *means* is per
product (ADR-0000 §6). Marketing data is org-wide analytics rather than per-person records, so
there is no natural TEAM or LOCATION reading of a campaign: an analyst either sees the
organization's marketing picture or they do not. The product therefore maps only the owner rung —
to the campaign row itself — and its read permission defaults to ORG in the catalogue.

Registering a resolver keeps the product consistent with the platform's scope machinery even
though it exercises only the ORG rung today: `ctx.apply` passes an ORG statement through
unchanged, and a narrower grant with no subject reference matches nothing rather than leaking.
"""

from __future__ import annotations

from app.models import Campaign
from platform_core.auth import column_scope_resolver, register_scope_resolver

PRODUCT = "marketing"


def register() -> None:
    """Register the resolver for the `marketing` product. Idempotent — safe to call at import."""
    register_scope_resolver(PRODUCT, column_scope_resolver(owner=Campaign.id))
