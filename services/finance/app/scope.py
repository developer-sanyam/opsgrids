"""How the `finance` product turns a scope rung into a query filter.

The scope ladder (`OWN < TEAM < LOCATION < ORG`) is universal, but what each rung *means* is
per product (ADR-0000 §6). Finance is an organization-level function: a company's books are not
divided into "your own" invoices, so finance data resolves at ORG and the narrower rungs have no
natural reading here. The resolver is registered for symmetry with the other products and to
name the owner column, but every finance grant is ORG — a narrower grant is refused at the route
floor, and `ctx.apply` at ORG returns the statement unchanged.

    OWN/TEAM/LOCATION -> no finance-specific reading; ORG is the only scope finance grants.
    ORG               -> the whole organization's receivable book.
"""

from __future__ import annotations

from app.models import Invoice
from platform_core.auth import column_scope_resolver, register_scope_resolver

PRODUCT = "finance"


def register() -> None:
    """Register the resolver for the `finance` product. Idempotent — safe to call at import."""
    register_scope_resolver(PRODUCT, column_scope_resolver(owner=Invoice.id))
