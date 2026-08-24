"""Entitlements resolves cross-tenant on a SELECT-only bypass reader — the 402 blocker.

auth-service calls `/internal/v1/organizations/{org}/entitlements` while it mints that
organization a token, before any tenant is bound: the token being minted is what would establish
one. `activations` carries a forced row-level policy `USING (organization_id = current_org())`,
so on the application role — which sets no GUC at that moment — the organization's real
activation is invisible and the endpoint returns only the platform catalogue. Every product's
routes then 402 at the gateway.

The fix reads on `atmora_product_directory`: BYPASSRLS so it resolves the activation with no
tenant set, SELECT-only on `product.activations` and `product.products` so a bug in that path
cannot write. These are the regression — the activated product must come back with no GUC bound —
and the guardrails that keep the bypass narrow (ADR-0002, "the cross-tenant reader").
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import selectors
from app.config import settings
from app.models import Activation
from platform_core.internal_auth import mint_service_token

pytestmark = pytest.mark.integration


def _service_token() -> dict[str, str]:
    """A freshly minted service token addressed to product-service, as auth sends."""
    return {
        "X-Internal-Token": mint_service_token(settings(), audience="product", caller="tests"),
        "X-Calling-Service": "tests",
    }


@pytest.fixture
def other_org() -> uuid.UUID:
    return uuid.uuid4()


@pytest_asyncio.fixture
async def activated(admin_session: AsyncSession, org_id: uuid.UUID) -> uuid.UUID:
    """One active `hrms` activation for `org_id`, seeded on the admin role.

    Written outside any tenant scope, the way the platform activates a product for a customer —
    which is exactly the state the entitlements read has to resolve before a tenant exists.
    """
    async with admin_session.begin():
        await admin_session.execute(text('TRUNCATE "product"."activations" CASCADE'))
        admin_session.add(Activation(organization_id=org_id, product_key="hrms", status="active"))
    return org_id


class TestTheReaderIsWhatWeThink:
    async def test_the_reader_is_the_bypass_role(self, directory_session: AsyncSession) -> None:
        """A regression that read on a NOBYPASSRLS role would be a false pass — it would see
        nothing for the same reason the bug does. Assert the reader genuinely bypasses."""
        row = (
            await directory_session.execute(
                text("SELECT current_user, rolbypassrls FROM pg_roles WHERE rolname = current_user")
            )
        ).one()
        assert row.current_user == "atmora_product_directory"
        assert row.rolbypassrls is True, (
            "the entitlements reader must bypass RLS, or it resolves nothing before a tenant "
            "is bound — the 402 blocker this exists to fix"
        )


class TestEntitlementsResolveWithNoTenant:
    async def test_an_activated_product_is_returned_with_no_guc_bound(
        self,
        directory_session: AsyncSession,
        activated: uuid.UUID,
    ) -> None:
        """The regression. This is the read auth-service makes at token-minting time, on the
        session that serves it in production — bypass, no tenant GUC. The activated product must
        come back, or the `ent` claim omits it and every one of its routes 402s."""
        keys = await selectors.entitlements_for(directory_session, activated)
        assert "hrms" in keys, (
            "an active activation was invisible to the cross-tenant reader with no tenant "
            "bound — this is the 402 blocker"
        )
        assert "core" in keys, "platform products must still be included"

    async def test_the_endpoint_returns_the_activation_the_way_login_calls_it(
        self,
        client: AsyncClient,
        activated: uuid.UUID,
    ) -> None:
        """End to end through the real route: a service token, the directory-session dependency,
        no tenant context — exactly the shape of auth-service's call while minting a token."""
        response = await client.get(
            f"/internal/v1/organizations/{activated}/entitlements",
            headers=_service_token(),
        )
        assert response.status_code == 200
        assert "hrms" in response.json()["entitlements"]

    async def test_the_app_role_with_no_tenant_sees_only_platform_products(
        self,
        unbound_app_session: AsyncSession,
        activated: uuid.UUID,
    ) -> None:
        """The companion, so the bug cannot silently return. On the application role with no GUC
        the forced policy hides the activation, so only the global catalogue comes back — which
        is precisely why this read cannot run on that role."""
        keys = await selectors.entitlements_for(unbound_app_session, activated)
        assert "hrms" not in keys, (
            "the application role saw an activation with no tenant bound — the policy on "
            "activations is not being enforced, and the directory reader would be pointless"
        )
        assert "core" in keys, "platform products come back on any role — they carry no policy"

    async def test_one_organizations_activation_is_not_returned_for_another(
        self,
        directory_session: AsyncSession,
        activated: uuid.UUID,
        other_org: uuid.UUID,
    ) -> None:
        """The bypass reads across tenants by id, not everything at once: resolving one
        organization must not hand back another's product access."""
        theirs = await selectors.entitlements_for(directory_session, other_org)
        assert "hrms" not in theirs
        assert "core" in theirs


class TestTheBypassStaysNarrow:
    async def test_the_reader_cannot_write_to_activations(
        self, directory_session: AsyncSession
    ) -> None:
        """SELECT-only. The reader bypasses row-level security, so the one thing standing
        between a bug in that path and a cross-tenant write is the table privilege — assert it
        is withheld."""
        with pytest.raises(Exception, match="permission denied"):
            await directory_session.execute(
                text("UPDATE product.activations SET status = 'tampered'")
            )

    async def test_the_reader_cannot_insert_into_activations(
        self, directory_session: AsyncSession
    ) -> None:
        with pytest.raises(Exception, match="permission denied"):
            await directory_session.execute(
                text(
                    "INSERT INTO product.activations "
                    "(id, organization_id, product_key, status) "
                    "VALUES (gen_random_uuid(), gen_random_uuid(), 'hrms', 'active')"
                )
            )

    async def test_the_reader_cannot_reach_another_service_schema(
        self, directory_session: AsyncSession
    ) -> None:
        """BYPASSRLS is not cross-schema. The reader is granted USAGE on `product` alone, so a
        product bypass role cannot become a back door into auth-service's credentials."""
        with pytest.raises(Exception, match="permission denied"):
            await directory_session.execute(text("SELECT 1 FROM auth.credentials"))
