"""MRR is monthly: a yearly subscription contributes a twelfth of its yearly price (ADR-0035).

The fold resolves a subscription's pinned price from catalog's plan-version document
(`price.resolve_plan_price`) and adds it into the org's `mrr_minor`, the platform MRR, the MRR
series and the admin "Per month" column. Until the 2026-10-06 list prices a yearly version could
only be a bespoke price; now every public flat plan has one that staff Add product can start, so a
yearly subscriber counted at the full yearly amount would overstate every one of those figures
twelvefold.

Driven through the real resolver over a mocked catalog transport (no network), and through the
real consumer path for the fold — the resolver is the only thing standing in for catalog.
"""

from __future__ import annotations

import uuid
from typing import Any

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import consumers, selectors, services
from app.config import settings
from app.models import RevenueEvent
from app.price import PriceResolver, resolve_plan_price
from tests.helpers import envelope, subscription_payload

YEARLY = "22222222-2222-2222-2222-222222222222"
MONTHLY = "33333333-3333-3333-3333-333333333333"
ONE_TIME = "44444444-4444-4444-4444-444444444444"
LEGACY = "55555555-5555-5555-5555-555555555555"
ODD_YEARLY = "66666666-6666-6666-6666-666666666666"


def _doc(version_id: str, interval: str | None, *amounts: int) -> dict[str, Any]:
    doc: dict[str, Any] = {
        "id": version_id,
        "plan_id": str(uuid.uuid4()),
        "currency": "INR",
        "prices": [
            {"kind": "flat", "amount_minor": amount, "currency": "INR"} for amount in amounts
        ]
        # Usage is never recurring revenue, whatever the cadence.
        + [{"kind": "usage", "amount_minor": None, "currency": "INR"}],
    }
    if interval is not None:
        doc["billing_interval"] = interval
    return doc


#: Catalog's plan-version documents, as its internal read returns them.
DOCS: dict[str, dict[str, Any]] = {
    # crm on the 2026-10-06 yearly list price: INR 30,000 a year.
    YEARLY: _doc(YEARLY, "year", 3000000),
    # crm on the 2026-10-06 monthly list price: INR 2,500 a month.
    MONTHLY: _doc(MONTHLY, "month", 250000),
    ONE_TIME: _doc(ONE_TIME, "one_time", 50000),
    # A document from before `billing_interval` was part of it: monthly, as everything then was.
    LEGACY: _doc(LEGACY, None, 50000),
    # Two flat lines that do not divide by twelve: 1,000,001 / 12 = 83,333.42 -> 83,333.
    ODD_YEARLY: _doc(ODD_YEARLY, "year", 1000000, 1),
}


def _catalog(request: httpx.Request) -> httpx.Response:
    assert request.headers.get("X-Internal-Token"), "a service token, addressed to catalog"
    version_id = request.url.path.rsplit("/", 1)[-1]
    if version_id not in DOCS:
        return httpx.Response(404)
    return httpx.Response(200, json=DOCS[version_id])


def _resolver() -> PriceResolver:
    async def _resolve(plan_version_id: str) -> tuple[int, str | None]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(_catalog)) as client:
            return await resolve_plan_price(settings(), plan_version_id, client=client)

    return _resolve


class TestTheResolvedPriceIsMonthly:
    @pytest.mark.parametrize(
        ("version_id", "expected"),
        [
            (MONTHLY, 250000),
            (YEARLY, 250000),
            (ODD_YEARLY, 83333),
            (LEGACY, 50000),
            (ONE_TIME, 0),
        ],
        ids=["month", "year", "year-rounded", "no-interval", "one-time"],
    )
    async def test_a_period_price_becomes_a_months(self, version_id: str, expected: int) -> None:
        amount, currency = await _resolver()(version_id)
        assert (amount, currency) == (expected, "INR")
        assert type(amount) is int, "money is integer minor units, never a float"

    async def test_rounding_is_half_up_to_the_minor_unit(self) -> None:
        from app.price import _monthly_minor

        assert _monthly_minor(102, "year") == 9, "8.5 rounds up"
        assert _monthly_minor(101, "year") == 8, "8.42 rounds down"
        assert _monthly_minor(3750000, "year") == 312500
        assert _monthly_minor(187500, "month") == 187500

    async def test_an_unresolvable_version_is_still_zero(self) -> None:
        assert await _resolver()(str(uuid.uuid4())) == (0, None)


async def _fold(env: dict[str, Any], resolver: PriceResolver) -> None:
    async def fold(session: AsyncSession, envelope_: dict[str, Any]) -> None:
        await services.fold_subscription_event(session, envelope_, resolve_price=resolver)

    await consumers.consume(env, fold)


def _activated(org: uuid.UUID, product: str, version_id: str) -> dict[str, Any]:
    return envelope(
        "commerce.subscription.activated",
        organization_id=org,
        payload=subscription_payload(
            org, product_key=product, state="active", plan_version_id=version_id
        ),
    )


@pytest.mark.integration
class TestAYearlySubscriberInMrr:
    async def test_counts_a_twelfth_of_the_year_in_every_mrr_figure(
        self,
        engine_ready: None,
        directory_session: AsyncSession,
        org_id: uuid.UUID,
        org_b: uuid.UUID,
    ) -> None:
        resolver = _resolver()
        # Org A: crm on the yearly list price. Org B: crm on the monthly one. Same product, same
        # money per month — so the same MRR.
        await _fold(_activated(org_id, "crm", YEARLY), resolver)
        await _fold(_activated(org_b, "crm", MONTHLY), resolver)

        states = {s.organization_id: s for s in await selectors.all_org_states(directory_session)}
        assert states[org_id].mrr_minor == 250000, "INR 30,000 a year is INR 2,500 a month"
        assert states[org_b].mrr_minor == 250000
        assert states[org_id].subscription_states["crm"]["amount_minor"] == 250000, (
            "the admin 'Per month' column reads this entry"
        )

        metrics = await selectors.platform_metrics(directory_session)
        assert metrics["mrr_minor"] == 500000, "not 3,250,000: the year is not a month's revenue"

        logged = (
            (
                await directory_session.execute(
                    select(RevenueEvent.amount_minor).where(RevenueEvent.organization_id == org_id)
                )
            )
            .scalars()
            .all()
        )
        assert logged == [250000], "the revenue log the MRR series replays carries the same"

        series = await selectors.metrics_series(directory_session, months=1)
        assert list(series["mrr_minor"].values()) == [500000]

    async def test_a_one_time_version_adds_nothing_to_mrr(
        self, engine_ready: None, directory_session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        await _fold(_activated(org_id, "crm", ONE_TIME), _resolver())
        (state,) = await selectors.all_org_states(directory_session)
        assert state.mrr_minor == 0
        assert state.active_products == ["crm"], "still entitled; only the revenue is zero"
