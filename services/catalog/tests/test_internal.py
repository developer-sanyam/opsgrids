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

    async def test_the_read_carries_the_plans_display_name(
        self, client, platform_session: AsyncSession
    ) -> None:
        """`plan_name` is additive: the pricing composite reads the pinned plan's name off the
        same document it already fetches for the price, with no second call."""
        async with platform_session.begin():
            version = await _published_version(platform_session)
        resp = await client.get(
            f"/internal/v1/plan-versions/{version.id}", headers={"X-Internal-Token": _token()}
        )
        assert resp.status_code == 200
        assert resp.json()["plan_name"] == "Standard"


class TestPlanVersionNames:
    """`GET /internal/v1/plan-versions?ids=...` — the batch behind console's subscription list,
    so N rows cost one call instead of N."""

    URL = "/internal/v1/plan-versions"

    async def test_it_names_many_versions_in_one_call(
        self, client, platform_session: AsyncSession
    ) -> None:
        op = operator_principal()
        async with platform_session.begin():
            starter = await services.create_plan(
                platform_session,
                op,
                PlanCreateIn(product_key="hrms", code="starter", name="Starter"),
                request=None,
            )
            growth = await services.create_plan(
                platform_session,
                op,
                PlanCreateIn(product_key="crm", code="growth", name="Growth"),
                request=None,
            )
            versions = []
            for plan in (starter, growth):
                version = await services.create_version(
                    platform_session,
                    op,
                    plan,
                    VersionCreateIn(
                        currency="USD",
                        prices=[PriceIn(kind="flat", amount_minor=1000, currency="USD")],
                    ),
                    request=None,
                )
                versions.append(
                    await services.publish_version(platform_session, op, version, request=None)
                )

        resp = await client.get(
            self.URL,
            params=[("ids", str(v.id)) for v in versions],
            headers={"X-Internal-Token": _token()},
        )
        assert resp.status_code == 200, resp.text
        by_id = {row["plan_version_id"]: row for row in resp.json()["versions"]}
        assert by_id[str(versions[0].id)]["plan_name"] == "Starter"
        assert by_id[str(versions[0].id)]["product_key"] == "hrms"
        assert by_id[str(versions[1].id)]["plan_name"] == "Growth"
        assert by_id[str(versions[1].id)]["product_key"] == "crm"

    async def test_an_unknown_id_is_simply_absent_not_a_404_for_the_batch(
        self, client, platform_session: AsyncSession
    ) -> None:
        async with platform_session.begin():
            version = await _published_version(platform_session)
        ghost = uuid.uuid4()
        resp = await client.get(
            self.URL,
            params=[("ids", str(version.id)), ("ids", str(ghost))],
            headers={"X-Internal-Token": _token()},
        )
        assert resp.status_code == 200
        ids = {row["plan_version_id"] for row in resp.json()["versions"]}
        assert ids == {str(version.id)}

    async def test_the_names_are_unfiltered_like_the_single_read(
        self, client, platform_session: AsyncSession
    ) -> None:
        """A bespoke version is named too — a name is not a commercial decision, and the caller
        (console's staff list) needs it for exactly the organizations that hold one."""
        async with platform_session.begin():
            version = await _published_version(platform_session, org=uuid.uuid4())
        resp = await client.get(
            self.URL, params=[("ids", str(version.id))], headers={"X-Internal-Token": _token()}
        )
        assert [row["plan_name"] for row in resp.json()["versions"]] == ["Standard"]

    async def test_ids_are_required_bounded_and_uuids(self, client) -> None:
        headers = {"X-Internal-Token": _token()}
        assert (await client.get(self.URL, headers=headers)).status_code == 422
        too_many = [("ids", str(uuid.uuid4())) for _ in range(201)]
        assert (await client.get(self.URL, params=too_many, headers=headers)).status_code == 422
        bad = await client.get(self.URL, params=[("ids", "not-a-uuid")], headers=headers)
        assert bad.status_code == 422

    async def test_without_a_service_token_it_is_refused(self, client) -> None:
        resp = await client.get(self.URL, params=[("ids", str(uuid.uuid4()))])
        assert resp.status_code == 401


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
