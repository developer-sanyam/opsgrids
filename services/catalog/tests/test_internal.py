"""The internal pin-lookup — how subscription resolves a plan version before pinning it.

Two properties: it is guarded by a service token (a request without one is refused), and it
returns the version **unfiltered**, including its price and audience pointer, so the calling
service can apply the published-and-visible rule itself.
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app import services
from app.config import settings
from app.schemas import PlanCreateIn, PriceIn, VersionCreateIn
from platform_core.internal_auth import mint_service_token
from tests.helpers import operator_principal

pytestmark = pytest.mark.integration


def _token() -> str:
    return mint_service_token(settings(), audience="catalog")


@contextlib.contextmanager
def _no_auth() -> Iterator[None]:
    yield


async def _published_version(session: AsyncSession, org: uuid.UUID | None = None):
    op = operator_principal()
    plan = await services.create_plan(
        session, op, PlanCreateIn(product_key="hrms", code="std", name="Standard"), request=None
    )
    version = await services.create_version(
        session,
        op,
        plan,
        VersionCreateIn(
            currency="USD",
            restricted_to_org_id=org,
            prices=[PriceIn(kind="flat", amount_minor=1500, currency="USD")],
        ),
        request=None,
    )
    return await services.publish_version(session, op, version, request=None)


class TestInternalPinLookup:
    async def test_a_service_token_resolves_a_version_with_its_price(
        self, client, platform_session: AsyncSession
    ) -> None:
        async with platform_session.begin():
            version = await _published_version(platform_session)

        resp = await client.get(
            f"/internal/v1/plan-versions/{version.id}",
            headers={"X-Internal-Token": _token()},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["id"] == str(version.id)
        assert body["product_key"] == "hrms"
        assert body["state"] == "published"
        assert body["currency"] == "USD"
        assert len(body["prices"]) == 1
        assert body["prices"][0]["amount_minor"] == 1500
        assert "id" in body["prices"][0], "the price id is what a subscription item references"

    async def test_the_read_is_unfiltered_by_audience(
        self, client, platform_session: AsyncSession
    ) -> None:
        """A bespoke version is returned with its `restricted_to_org_id` intact — the internal
        read does not apply the customer visibility filter; the caller does."""
        other = uuid.uuid4()
        async with platform_session.begin():
            version = await _published_version(platform_session, org=other)

        resp = await client.get(
            f"/internal/v1/plan-versions/{version.id}",
            headers={"X-Internal-Token": _token()},
        )
        assert resp.status_code == 200
        assert resp.json()["restricted_to_org_id"] == str(other)

    async def test_without_a_service_token_it_is_refused(self, client) -> None:
        resp = await client.get(f"/internal/v1/plan-versions/{uuid.uuid4()}")
        assert resp.status_code == 401

    async def test_an_unknown_version_is_not_found(self, client) -> None:
        resp = await client.get(
            f"/internal/v1/plan-versions/{uuid.uuid4()}",
            headers={"X-Internal-Token": _token()},
        )
        assert resp.status_code == 404


class TestPublicFlatPlans:
    """The console pricing screen's baseline read: one row per product's public flat plan."""

    async def test_it_lists_the_newest_published_public_flat_version_per_product(
        self, client, platform_session: AsyncSession
    ) -> None:
        op = operator_principal()
        async with platform_session.begin():
            plan = await services.create_plan(
                platform_session,
                op,
                PlanCreateIn(product_key="hrms", code="flat", name="People"),
                request=None,
            )
            v1 = await services.create_version(
                platform_session,
                op,
                plan,
                VersionCreateIn(
                    currency="INR",
                    prices=[PriceIn(kind="flat", amount_minor=100000, currency="INR")],
                ),
                request=None,
            )
            await services.publish_version(platform_session, op, v1, request=None)
            v2 = await services.create_version(
                platform_session,
                op,
                plan,
                VersionCreateIn(
                    currency="INR",
                    prices=[PriceIn(kind="flat", amount_minor=120000, currency="INR")],
                ),
                request=None,
            )
            await services.publish_version(platform_session, op, v2, request=None)
            # A bespoke, restricted version must never show up as anyone's "public" price.
            bespoke = await services.create_version(
                platform_session,
                op,
                plan,
                VersionCreateIn(
                    currency="INR",
                    restricted_to_org_id=uuid.uuid4(),
                    prices=[PriceIn(kind="flat", amount_minor=1, currency="INR")],
                ),
                request=None,
            )
            await services.publish_version(platform_session, op, bespoke, request=None)

        resp = await client.get(
            "/internal/v1/public-flat-plans", headers={"X-Internal-Token": _token()}
        )
        assert resp.status_code == 200
        rows = {row["product_key"]: row for row in resp.json()["plans"]}
        assert rows["hrms"]["plan_version_id"] == str(v2.id), "the newest published version wins"
        assert rows["hrms"]["amount_minor"] == 120000
        assert rows["hrms"]["product_name"] == "People"

    async def test_without_a_service_token_it_is_refused(self, client) -> None:
        resp = await client.get("/internal/v1/public-flat-plans")
        assert resp.status_code == 401
