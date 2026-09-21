"""Engage is in the catalogue, and registered as something that can actually be turned on.

The nine `soon` rows the catalogue shipped with are the trap this registration must not fall
into: `activate` refuses any product marked `soon`, so a service that exists would sit behind a
gate nothing can open — `POST /products/activations` refusing, and every Engage route answering
402 at the gateway, which is exactly how the storefront registration was found missing.

So the load-bearing test is not "the row is there" but "the activation path accepts it". Reading
`status` back from the row would only assert the string the migration wrote; calling `activate`
asserts what that string is for.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app import selectors, services
from platform_core.auth import Principal, Scope, ScopeContext

pytestmark = pytest.mark.integration

KEY = "engage"


def _ctx(org_id: uuid.UUID) -> ScopeContext:
    """A caller as `require("core.product.activation.write", minimum=ORG)` would have built."""
    principal = Principal(
        sub=uuid.uuid4(),
        organization_id=org_id,
        membership_id=uuid.uuid4(),
        perms=["core.product.activation.write:ORG"],
        entitlements=["core"],
    )
    return ScopeContext(
        principal=principal, scope=Scope.ORG, permission="core.product.activation.write"
    )


async def test_engage_is_registered_as_a_product_somebody_buys(session: AsyncSession) -> None:
    async with session.begin():
        product = await selectors.get_product(session, KEY)

    assert product is not None
    assert product.name == "OpsGrids Engage"
    # Not `is_platform`: notification is the plumbing that ships with the account, Engage is the
    # thing a customer subscribes to. Marking it platform would entitle every organization
    # automatically and there would be nothing to sell.
    assert product.is_platform is False


async def test_an_organization_can_activate_engage(
    session: AsyncSession, org_id: uuid.UUID
) -> None:
    async with session.begin():
        activation = await services.activate(session, _ctx(org_id), product_key=KEY)

    assert activation.entitles


async def test_activating_engage_entitles_it(session: AsyncSession, org_id: uuid.UUID) -> None:
    """The end of the chain the registration exists for: the `ent` claim the gateway reads."""
    async with session.begin():
        await services.activate(session, _ctx(org_id), product_key=KEY)
        keys = await selectors.entitlements_for(session, org_id)

    assert KEY in keys
