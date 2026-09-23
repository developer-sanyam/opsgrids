"""Test helpers shared between conftest and the test modules.

Kept out of `conftest.py` because conftest is loaded by pytest rather than imported, so importing
*from* it is fragile — this module is a plain import that works the same way everywhere.

The customer principals are forged directly, the way the gateway tests forge theirs: the real
token minter lives in auth-service and the gateway resolves permissions. What matters to this
service is the organization and the permission list the gateway would have forwarded. The
internal-API tests mint a **real** service token, so the `require_service_token` guard is
exercised rather than stubbed.
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import Iterator

from platform_core.auth import Principal, get_principal
from platform_core.config import ServiceSettings
from platform_core.internal_auth import mint_service_token

#: What the product gateway stamps on a customer's token.
APP_AUDIENCE = "app"
#: What the operator gateway stamps. `require_operator` accepts only this.
OPERATOR_AUDIENCE = "operator"

#: The permissions the billing read routes require, at ORG scope.
INVOICE_READ = "core.billing.invoice.read:ORG"
CREDIT_READ = "core.billing.credit.read:ORG"


def customer_principal(org_id: uuid.UUID, *, perms: list[str] | None = None) -> Principal:
    """A customer as the product gateway would have forwarded them.

    Holds both billing read permissions at ORG scope by default, so a test can vary the
    organization while keeping the grant fixed — which is what the cross-tenant read needs."""
    return Principal(
        sub=uuid.uuid4(),
        organization_id=org_id,
        membership_id=uuid.uuid4(),
        audience=APP_AUDIENCE,
        entitlements=["core"],
        perms=perms if perms is not None else [INVOICE_READ, CREDIT_READ],
    )


def service_token_headers(settings: ServiceSettings) -> dict[str, str]:
    """A real short-lived service token addressed to billing — what an internal caller sends."""
    token = mint_service_token(settings, audience=settings.service_name, caller="tests")
    return {"X-Internal-Token": token}


def operator_principal(*, staff: bool = True, roles: list[str] | None = None) -> Principal:
    """A staff operator as the operator gateway would have forwarded them.

    `audience="operator"` is the marker `require_operator` checks. `staff=False` swaps the
    audience to a product token, producing the negative case a staff route must refuse with 404.
    `roles` defaults to `["owner"]`, which holds every route's role requirement (ADR-0003
    addendum): `["owner", "billing_ops"]` is credit-ledger's actual matrix entry.
    """
    return Principal(
        sub=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        membership_id=uuid.uuid4(),
        audience=OPERATOR_AUDIENCE if staff else APP_AUDIENCE,
        roles=roles if roles is not None else ["owner"],
    )


@contextlib.contextmanager
def as_principal(principal: Principal) -> Iterator[None]:
    """Override the resolved caller for one block — how the audience/role boundary is exercised."""
    from app.main import app

    app.dependency_overrides[get_principal] = lambda: principal
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_principal, None)
