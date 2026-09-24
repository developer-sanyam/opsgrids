"""Console's two internal reads/writes on subscription: listing an organization's subscriptions
for the pricing screen, and the caller allow-list on the operator re-pin (`change-plan`).

Both are narrowed to console specifically (`api/internal.py:require_console_caller`) — a service
token from anywhere else, however genuine, is refused with the same 401 a missing token gets.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app import services
from app.api.internal import get_product_registry
from app.config import settings
from app.main import app
from app.schemas import SubscribeIn
from platform_core.auth import Scope, ScopeContext
from platform_core.internal_auth import mint_service_token
from tests.helpers import FakeCatalog, FakeRegistry, customer_principal, make_version

pytestmark = pytest.mark.integration


def _ctx(org_id: uuid.UUID) -> ScopeContext:
    return ScopeContext(
        principal=customer_principal(org_id),
        scope=Scope.ORG,
        permission="core.product.activation.write",
    )


def _console_headers() -> dict[str, str]:
    return {
        "X-Internal-Token": mint_service_token(
            settings(), audience="subscription", caller="console"
        )
    }


def _foreign_headers() -> dict[str, str]:
    return {
        "X-Internal-Token": mint_service_token(
            settings(), audience="subscription", caller="some-other-service"
        )
    }


class TestListSubscriptionsForOperator:
    async def test_it_lists_every_subscription_the_organization_holds(
        self, client, session: AsyncSession, catalog: FakeCatalog, org_id: uuid.UUID
    ) -> None:
        hrms = catalog.add(make_version(product_key="hrms", currency="USD", amount_minor=1000))
        crm = catalog.add(make_version(product_key="crm", currency="USD", amount_minor=2000))
        async with session.begin():
            await services.subscribe(
                session,
                _ctx(org_id),
                catalog,
                SubscribeIn(product_key="hrms", plan_version_id=hrms.id),
            )
        async with session.begin():
            await services.subscribe(
                session,
                _ctx(org_id),
                catalog,
                SubscribeIn(product_key="crm", plan_version_id=crm.id),
            )

        resp = await client.get(
            f"/internal/v1/organizations/{org_id}/subscriptions", headers=_console_headers()
        )
        assert resp.status_code == 200
        rows = resp.json()
        assert {r["product_key"] for r in rows} == {"hrms", "crm"}
        assert all(uuid.UUID(r["id"]) for r in rows)

    async def test_it_is_empty_for_an_organization_with_no_subscriptions(
        self, client, org_id: uuid.UUID
    ) -> None:
        resp = await client.get(
            f"/internal/v1/organizations/{org_id}/subscriptions", headers=_console_headers()
        )
        assert resp.status_code == 200
        assert resp.json() == []

    async def test_without_a_service_token_it_is_refused(self, client, org_id: uuid.UUID) -> None:
        resp = await client.get(f"/internal/v1/organizations/{org_id}/subscriptions")
        assert resp.status_code == 401

    async def test_a_service_token_from_another_caller_is_refused(
        self, client, org_id: uuid.UUID
    ) -> None:
        resp = await client.get(
            f"/internal/v1/organizations/{org_id}/subscriptions", headers=_foreign_headers()
        )
        assert resp.status_code == 401


class TestChangePlanAsOperatorCallerAllowList:
    async def test_a_service_token_from_another_caller_is_refused(
        self, client, catalog: FakeCatalog, org_id: uuid.UUID
    ) -> None:
        version = catalog.add(make_version(product_key="hrms", amount_minor=1000))
        resp = await client.post(
            f"/internal/v1/subscriptions/{uuid.uuid4()}/change-plan",
            json={
                "organization_id": str(org_id),
                "plan_version_id": str(version.id),
                "operator_id": "operator:test",
            },
            headers=_foreign_headers(),
        )
        assert resp.status_code == 401

    async def test_consoles_own_token_reaches_the_business_logic(
        self, client, session: AsyncSession, catalog: FakeCatalog, org_id: uuid.UUID
    ) -> None:
        """Not a full re-pin test (that is `test_bundles.py`'s job) — just that the caller check
        does not block console's own token, so a missing subscription 404s rather than 401ing."""
        version = catalog.add(make_version(product_key="hrms", amount_minor=1000))
        app.dependency_overrides[get_product_registry] = lambda: FakeRegistry()
        try:
            resp = await client.post(
                f"/internal/v1/subscriptions/{uuid.uuid4()}/change-plan",
                json={
                    "organization_id": str(org_id),
                    "plan_version_id": str(version.id),
                    "operator_id": "operator:test",
                },
                headers=_console_headers(),
            )
        finally:
            app.dependency_overrides.pop(get_product_registry, None)
        assert resp.status_code == 404, resp.text
