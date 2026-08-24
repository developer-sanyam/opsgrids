"""Test helpers shared between conftest and the test modules.

Kept out of `conftest.py` because conftest is loaded by pytest rather than imported, so
importing *from* it is fragile — this module is a plain import that works the same way
everywhere.

The tokens minted here are forged directly, the way the gateway tests forge theirs: the real
`stf` staff-token minter does not exist yet (it arrives with the operator console, a later
phase). What matters for this service is the audience the operator gateway stamps, so these
principals carry it.
"""

from __future__ import annotations

import uuid

from platform_core.auth import Principal

#: What the product gateway stamps on a customer's token.
APP_AUDIENCE = "app"
#: What the operator gateway stamps. `require_operator` accepts only this.
OPERATOR_AUDIENCE = "operator"


def customer_principal(org_id: uuid.UUID, *, perms: list[str] | None = None) -> Principal:
    """A customer as the product gateway would have forwarded them.

    Holds `core.product.catalogue.read` by default — the marketplace-browse permission the
    plan-read routes reuse — so a test can vary the organization while keeping the permission
    fixed, which is what the bespoke-visibility test needs.
    """
    return Principal(
        sub=uuid.uuid4(),
        organization_id=org_id,
        membership_id=uuid.uuid4(),
        audience=APP_AUDIENCE,
        entitlements=["core"],
        perms=perms if perms is not None else ["core.product.catalogue.read:ORG"],
    )


def operator_principal(*, staff: bool = True) -> Principal:
    """A staff operator as the operator gateway would have forwarded them.

    `audience="operator"` is the marker `require_operator` checks. `staff` toggles the audience
    so the same helper produces the negative case — a non-operator (product) token that must be
    refused with 404.
    """
    return Principal(
        sub=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        membership_id=uuid.uuid4(),
        audience=OPERATOR_AUDIENCE if staff else APP_AUDIENCE,
    )
