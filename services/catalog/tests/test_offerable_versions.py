"""`GET /internal/v1/organizations/{org}/offerable-versions` — console's plan picker.

What is pinned:

* **Console only.** A valid token for this service from any other caller, or none, is refused.
* **Audience.** Another organization's bespoke version is never returned; this organization's own,
  and the public ones, are. This is the filter that stands in for row-level security on a global
  table, so it carries its own negative test here.
* **Offerable means current.** Active plans only, published versions only, and a version is dropped
  when a newer published version of the same plan has the same audience, cadence and currency
  (nothing grandfathers on publish, so the old one is otherwise still pinnable by id). A version
  withdrawn on the Plans page (ADR-0030) is not published, so it is not offered.
* **Everything a picker needs in one row** — plan identity beside the version's, prices with their
  ids and amounts, the feature sheet.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app import services
from app.config import settings
from app.models import Plan
from app.schemas import FeatureIn, PlanCreateIn, PriceIn, VersionCreateIn
from platform_core.internal_auth import mint_service_token
from tests.helpers import operator_principal

pytestmark = pytest.mark.integration


def _headers(caller: str | None = "console") -> dict[str, str]:
    if caller is None:
        return {}
    return {"X-Internal-Token": mint_service_token(settings(), audience="catalog", caller=caller)}


def _url(org: uuid.UUID) -> str:
    return f"/internal/v1/organizations/{org}/offerable-versions"


async def _plan(
    session: AsyncSession, *, product: str = "hrms", code: str = "growth", name: str = "Growth"
) -> Plan:
    return await services.create_plan(
        session,
        operator_principal(),
        PlanCreateIn(product_key=product, code=code, name=name),
        request=None,
    )


async def _publish(
    session: AsyncSession,
    plan: Plan,
    *,
    amount_minor: int = 1500,
    currency: str = "USD",
    interval: str = "month",
    org: uuid.UUID | None = None,
    features: list[FeatureIn] | None = None,
    publish: bool = True,
) -> Any:
    op = operator_principal()
    version = await services.create_version(
        session,
        op,
        plan,
        VersionCreateIn(
            billing_interval=interval,
            currency=currency,
            restricted_to_org_id=org,
            prices=[PriceIn(kind="flat", amount_minor=amount_minor, currency=currency)],
            features=features or [],
        ),
        request=None,
    )
    return (
        await services.publish_version(session, op, version, request=None) if publish else version
    )


async def _offered(client: AsyncClient, org: uuid.UUID, product: str = "hrms") -> list[dict]:
    response = await client.get(_url(org), params={"product_key": product}, headers=_headers())
    assert response.status_code == 200, response.text
    return list(response.json()["versions"])


class TestAccess:
    @pytest.mark.parametrize("caller", [None, "subscription", "billing", "tests"])
    async def test_only_console_may_read_it(
        self, client: AsyncClient, platform_session: AsyncSession, caller: str | None
    ) -> None:
        async with platform_session.begin():
            await _publish(platform_session, await _plan(platform_session))
        response = await client.get(
            _url(uuid.uuid4()), params={"product_key": "hrms"}, headers=_headers(caller)
        )
        assert response.status_code == 401, response.text
        assert "prices" not in response.text

    async def test_a_product_key_is_required_and_bounded(self, client: AsyncClient) -> None:
        missing = await client.get(_url(uuid.uuid4()), headers=_headers())
        odd = await client.get(
            _url(uuid.uuid4()), params={"product_key": "../x"}, headers=_headers()
        )
        assert (missing.status_code, odd.status_code) == (422, 422)


class TestWhatIsOffered:
    async def test_a_row_carries_the_plan_and_the_version_with_prices_and_features(
        self, client: AsyncClient, platform_session: AsyncSession
    ) -> None:
        org = uuid.uuid4()
        async with platform_session.begin():
            plan = await _plan(platform_session)
            version = await _publish(
                platform_session,
                plan,
                features=[FeatureIn(feature_key="trial_days", value=30)],
            )
        (row,) = await _offered(client, org)
        assert row["plan_id"] == str(plan.id)
        assert (row["plan_code"], row["plan_name"], row["product_key"]) == (
            "growth",
            "Growth",
            "hrms",
        )
        assert row["plan_version_id"] == str(version.id)
        assert (row["version_no"], row["state"]) == (1, "published")
        assert (row["billing_interval"], row["currency"]) == ("month", "USD")
        assert row["restricted_to_org_id"] is None and row["published_at"]
        (price,) = row["prices"]
        assert price["kind"] == "flat" and price["amount_minor"] == 1500
        assert uuid.UUID(price["id"])  # the price id a subscription item references
        assert {"feature_key": "trial_days", "value": 30} in row["features"]

    async def test_drafts_retired_plans_and_other_products_are_not_offered(
        self, client: AsyncClient, platform_session: AsyncSession
    ) -> None:
        async with platform_session.begin():
            live = await _plan(platform_session, code="live", name="Live")
            await _publish(platform_session, live)
            draft_only = await _plan(platform_session, code="draft", name="Draft only")
            await _publish(platform_session, draft_only, publish=False)
            retired = await _plan(platform_session, code="gone", name="Gone")
            await _publish(platform_session, retired)
            await services.retire_plan(
                platform_session, operator_principal(), retired, request=None
            )
            other = await _plan(platform_session, product="crm", code="crm", name="CRM")
            await _publish(platform_session, other)
        rows = await _offered(client, uuid.uuid4())
        assert [r["plan_name"] for r in rows] == ["Live"]
        assert [r["plan_name"] for r in await _offered(client, uuid.uuid4(), "crm")] == ["CRM"]

    async def test_another_organizations_bespoke_price_is_never_returned(
        self, client: AsyncClient, platform_session: AsyncSession
    ) -> None:
        org_a, org_b = uuid.uuid4(), uuid.uuid4()
        async with platform_session.begin():
            plan = await _plan(platform_session)
            public = await _publish(platform_session, plan, amount_minor=2000)
            bespoke_a = await _publish(platform_session, plan, amount_minor=900, org=org_a)
        for_a = {r["plan_version_id"] for r in await _offered(client, org_a)}
        for_b = {r["plan_version_id"] for r in await _offered(client, org_b)}
        assert for_a == {str(public.id), str(bespoke_a.id)}
        assert for_b == {str(public.id)}
        # And not by any other field either: org B's whole answer carries no trace of A's price.
        raw = (
            await client.get(_url(org_b), params={"product_key": "hrms"}, headers=_headers())
        ).text
        assert str(bespoke_a.id) not in raw and str(org_a) not in raw

    async def test_a_superseded_public_version_is_dropped(
        self, client: AsyncClient, platform_session: AsyncSession
    ) -> None:
        """Publishing v2 does not grandfather v1: v1 stays published and pinnable by id. Offering
        both would let a switch land a customer on a price the catalogue has moved past."""
        async with platform_session.begin():
            plan = await _plan(platform_session)
            await _publish(platform_session, plan, amount_minor=1000)
            v2 = await _publish(platform_session, plan, amount_minor=1200)
        (row,) = await _offered(client, uuid.uuid4())
        assert row["plan_version_id"] == str(v2.id) and row["version_no"] == 2

    async def test_supersession_is_per_audience_cadence_and_currency(
        self, client: AsyncClient, platform_session: AsyncSession
    ) -> None:
        """One currency and one cadence per subscription, so a newer yearly or euro version does
        not supersede the monthly dollar one — and a bespoke version is its own audience."""
        org = uuid.uuid4()
        async with platform_session.begin():
            plan = await _plan(platform_session)
            monthly = await _publish(platform_session, plan, amount_minor=1000)
            yearly = await _publish(platform_session, plan, amount_minor=10000, interval="year")
            euro = await _publish(platform_session, plan, amount_minor=900, currency="EUR")
            bespoke = await _publish(platform_session, plan, amount_minor=800, org=org)
            newer = await _publish(platform_session, plan, amount_minor=1100)
        offered = {r["plan_version_id"] for r in await _offered(client, org)}
        assert offered == {str(yearly.id), str(euro.id), str(bespoke.id), str(newer.id)}
        assert str(monthly.id) not in offered

    async def test_a_withdrawn_version_is_not_offered(
        self, client: AsyncClient, platform_session: AsyncSession
    ) -> None:
        """Withdraw (ADR-0030) is how the Plans page stops selling one version; the staff plan
        switch (ADR-0029) must honour it, public or negotiated alike."""
        org = uuid.uuid4()
        async with platform_session.begin():
            plan = await _plan(platform_session)
            public = await _publish(platform_session, plan, amount_minor=1500)
            bespoke = await _publish(platform_session, plan, amount_minor=900, org=org)
            kept = await _plan(platform_session, code="kept", name="Kept")
            still_on_sale = await _publish(platform_session, kept)
            for version in (public, bespoke):
                await services.withdraw_version(
                    platform_session, operator_principal(), version, request=None
                )
        offered = {r["plan_version_id"] for r in await _offered(client, org)}
        assert offered == {str(still_on_sale.id)}

    async def test_the_same_org_filter_holds_for_a_product_with_no_plans(
        self, client: AsyncClient
    ) -> None:
        assert await _offered(client, uuid.uuid4(), "nothing") == []
