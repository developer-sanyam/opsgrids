"""Subscription against the catalogue the 2026-10-06 list prices leave (ADR-0035).

Catalog's `b88a833020dc` gives every public flat plan two new published versions — the new yearly
price, then the new monthly one — and leaves the old monthly version published, because a
subscription pins a version and a published version is never edited. This module pins what that
means on subscription's side, with the fake catalog applying catalog's own offerable rule to that
exact shape (one plan: v1 the old monthly price, v2 the new yearly one, v3 the new monthly one):

* **An existing subscriber keeps the price they are on.** Nothing re-pins them; their Change plan
  picker lists v1 first as "no longer offered", v3 as a plan they can take, and v2 blocked with the
  write's own `cadence` code.
* **A monthly subscription cannot become a yearly one** — the customer write, its preview and the
  staff write all refuse with `cadence`, and change nothing. Moving a customer to yearly is a cancel
  and a new subscription, which is not built (a cancelled row cannot be replaced yet).
* **A new subscription can start on either cadence**: the customer path (the dashboard's Start
  trial) and the staff Add product path both accept the yearly version, and its period is a year.
* **…but never on the old price.** v1 stays published for the subscribers pinned to it, so the
  customer subscribe write enforces catalog's offerable set the way change-plan does: v1 by id —
  from a dashboard tab loaded before the deploy, or a guess from `version_no` — is refused
  `not_offerable` and nothing is created.
* **A yearly subscription is billed one period a year** by the billing run.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app import domain, services
from app.api.internal import get_product_registry
from app.billing_run import BillingRunSweep
from app.config import settings
from app.main import app
from app.models import STATE_ACTIVE, STATE_TRIAL, Subscription, SubscriptionItem
from app.schemas import CatalogPlanVersion, SubscribeIn
from platform_core.auth import Scope, ScopeContext, get_principal
from platform_core.internal_auth import mint_service_token
from tests.billing_fakes import FakeBilling
from tests.helpers import MANAGE, READ, FakeCatalog, FakeRegistry, customer_principal, make_version

pytestmark = pytest.mark.integration

#: The org owner: change plans, read subscriptions, read invoices.
OWNER = [f"{MANAGE}:ORG", f"{READ}:ORG", "core.billing.invoice.read:ORG"]
OPERATOR = "operator:11111111-1111-1111-1111-111111111111"
PRODUCT = "crm"


def _revenue(
    plan_id: uuid.UUID, version_no: int, amount_minor: int, interval: str
) -> CatalogPlanVersion:
    return make_version(
        product_key=PRODUCT,
        currency="INR",
        amount_minor=amount_minor,
        billing_interval=interval,
        plan_id=plan_id,
        plan_name="Revenue",
        version_no=version_no,
    )


def _repriced(catalog: FakeCatalog) -> tuple[CatalogPlanVersion, ...]:
    """The 'Revenue' plan after the migration: (old monthly, new yearly, new monthly)."""
    plan_id = uuid.uuid4()
    old = catalog.add(_revenue(plan_id, 1, 199900, "month"))
    yearly = catalog.add(_revenue(plan_id, 2, 3000000, "year"))
    monthly = catalog.add(_revenue(plan_id, 3, 250000, "month"))
    return old, yearly, monthly


def _as(org_id: uuid.UUID) -> None:
    app.dependency_overrides[get_principal] = lambda: customer_principal(org_id, perms=OWNER)


def _ctx(org_id: uuid.UUID) -> ScopeContext:
    return ScopeContext(principal=customer_principal(org_id), scope=Scope.ORG, permission=MANAGE)


def _internal() -> dict[str, str]:
    return {
        "X-Internal-Token": mint_service_token(
            settings(), audience="subscription", caller="console"
        )
    }


def _code(response: Response) -> str | None:
    errors = response.json().get("errors") or []
    return errors[0].get("code") if errors else None


async def _subscribe_before_the_reprice(
    session: AsyncSession, catalog: FakeCatalog, org_id: uuid.UUID, *, trial: bool
) -> tuple[uuid.UUID, tuple[CatalogPlanVersion, ...]]:
    """A customer who started on the old monthly price, then the catalogue was re-priced."""
    plan_id = uuid.uuid4()
    old = catalog.add(_revenue(plan_id, 1, 199900, "month"))
    async with session.begin():
        sub = await services.subscribe(
            session,
            _ctx(org_id),
            catalog,
            SubscribeIn(
                product_key=PRODUCT, plan_version_id=old.id, trial_days=14 if trial else None
            ),
        )
        sub_id = sub.id
    yearly = catalog.add(_revenue(plan_id, 2, 3000000, "year"))
    monthly = catalog.add(_revenue(plan_id, 3, 250000, "month"))
    return sub_id, (old, yearly, monthly)


async def _read(session: AsyncSession, subscription_id: uuid.UUID) -> Subscription:
    session.expunge_all()
    async with session.begin():
        return await services.require_subscription(session, subscription_id)


@pytest.fixture(autouse=True)
def _registry() -> Any:
    app.dependency_overrides[get_product_registry] = lambda: FakeRegistry()
    yield
    app.dependency_overrides.pop(get_product_registry, None)


# ---------------------------------------------------------------------- existing subscribers


class TestAnExistingSubscriber:
    async def test_keeps_the_old_price_and_is_offered_the_new_monthly_one(
        self,
        client: AsyncClient,
        session: AsyncSession,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
    ) -> None:
        sub_id, (old, yearly, monthly) = await _subscribe_before_the_reprice(
            session, catalog, org_id, trial=False
        )
        sub = await _read(session, sub_id)
        assert sub.plan_version_id == old.id, "publishing a price re-pins nobody"
        assert [i.price_id for i in sub.items] == [old.prices[0].id]

        _as(org_id)
        response = await client.get(f"/api/v1/subscriptions/{sub_id}/plan-options")
        assert response.status_code == 200, response.text
        body = response.json()
        options = body["options"]
        assert options[0]["plan_version_id"] == str(old.id), "the plan in force heads the list"
        by_id = {o["plan_version_id"]: o for o in options}
        assert set(by_id) == {str(old.id), str(yearly.id), str(monthly.id)}
        assert by_id[str(old.id)]["is_current"] is True
        assert "no longer offered" in by_id[str(old.id)]["blocked_reason"]
        assert by_id[str(monthly.id)]["blocked_code"] is None
        assert by_id[str(monthly.id)]["billing_interval"] == "month"
        assert by_id[str(yearly.id)]["blocked_code"] == domain.CODE_CADENCE
        assert by_id[str(yearly.id)]["billing_interval"] == "year"
        # A list price is never sent to a tenant, old or new (ADR-0034 §5).
        assert "250000" not in response.text and "3000000" not in response.text
        assert "199900" not in response.text

    async def test_a_monthly_subscription_cannot_switch_to_the_yearly_version(
        self,
        client: AsyncClient,
        session: AsyncSession,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
    ) -> None:
        sub_id, (old, yearly, _monthly) = await _subscribe_before_the_reprice(
            session, catalog, org_id, trial=False
        )
        _as(org_id)
        preview = await client.post(
            f"/api/v1/subscriptions/{sub_id}/change-plan/preview",
            json={"plan_version_id": str(yearly.id)},
        )
        assert preview.status_code == 200 and preview.json()["refusal_code"] == domain.CODE_CADENCE

        customer = await client.post(
            f"/api/v1/subscriptions/{sub_id}/change-plan",
            json={"plan_version_id": str(yearly.id)},
        )
        assert customer.status_code == 422, customer.text
        assert _code(customer) == domain.CODE_CADENCE
        assert "cancel and subscribe" in customer.json()["detail"]

        staff = await client.post(
            f"/internal/v1/subscriptions/{sub_id}/change-plan",
            json={
                "organization_id": str(org_id),
                "plan_version_id": str(yearly.id),
                "operator_id": OPERATOR,
                "timing": "next_period",
            },
            headers=_internal(),
        )
        assert staff.status_code == 422, staff.text
        assert _code(staff) == domain.CODE_CADENCE

        sub = await _read(session, sub_id)
        assert (sub.plan_version_id, sub.billing_interval, sub.pending_plan_version_id) == (
            old.id,
            "month",
            None,
        ), "a refusal changes nothing"

    async def test_a_trial_on_the_old_price_can_move_to_the_new_monthly_one(
        self,
        client: AsyncClient,
        session: AsyncSession,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
    ) -> None:
        sub_id, (_old, _yearly, monthly) = await _subscribe_before_the_reprice(
            session, catalog, org_id, trial=True
        )
        _as(org_id)
        response = await client.post(
            f"/api/v1/subscriptions/{sub_id}/change-plan",
            json={"plan_version_id": str(monthly.id)},
        )
        assert response.status_code == 200, response.text
        sub = await _read(session, sub_id)
        assert (sub.state, sub.plan_version_id) == (STATE_TRIAL, monthly.id)
        assert [i.price_id for i in sub.items] == [monthly.prices[0].id]


# ---------------------------------------------------------------------- new subscribers


class TestANewSubscriber:
    @pytest.mark.parametrize(
        ("which", "interval", "days"), [(2, "month", 30), (1, "year", 365)], ids=["month", "year"]
    )
    async def test_the_customer_path_starts_on_either_new_version(
        self,
        client: AsyncClient,
        session: AsyncSession,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
        which: int,
        interval: str,
        days: int,
    ) -> None:
        """`POST /api/v1/subscriptions` with the body the dashboard's Start trial posts. The
        dashboard itself always posts the new monthly version (its picker has no cadence choice);
        the API takes the yearly one too."""
        version = _repriced(catalog)[which]
        _as(org_id)
        response = await client.post(
            "/api/v1/subscriptions",
            json={"product_key": PRODUCT, "plan_version_id": str(version.id), "trial_days": 14},
        )
        assert response.status_code == 201, response.text
        sub = await _read(session, uuid.UUID(response.json()["id"]))
        assert (sub.state, sub.plan_version_id, sub.billing_interval) == (
            STATE_TRIAL,
            version.id,
            interval,
        )
        assert sub.current_period_start is not None and sub.current_period_end is not None
        assert sub.current_period_end - sub.current_period_start == timedelta(days=days)
        assert [i.price_id for i in sub.items] == [version.prices[0].id]

    @pytest.mark.parametrize("trial_days", [14, None], ids=["trial", "paid"])
    async def test_the_customer_path_refuses_the_old_price_by_id(
        self,
        client: AsyncClient,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
        trial_days: int | None,
    ) -> None:
        """v1 is still published (its subscribers stay pinned to it) and the customer's own plan
        read still lists its id. A dashboard tab loaded before the deploy posts exactly this body;
        so does anyone who reads `version_no`. Started on v1, a customer would keep the old price
        for good — nothing re-pins a subscription."""
        old, _yearly, _monthly = _repriced(catalog)
        _as(org_id)
        response = await client.post(
            "/api/v1/subscriptions",
            json={"product_key": PRODUCT, "plan_version_id": str(old.id), "trial_days": trial_days},
        )
        assert response.status_code == 422, response.text
        assert _code(response) == domain.CODE_NOT_OFFERABLE
        assert catalog.offerable_calls == 1

        listed = await client.get("/api/v1/subscriptions")
        assert listed.status_code == 200 and listed.json() == [], "nothing was created"

    async def test_a_price_negotiated_for_this_organization_is_still_taken(
        self,
        client: AsyncClient,
        session: AsyncSession,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
        other_org: uuid.UUID,
    ) -> None:
        """The offerable set keeps the newest version per audience: a bespoke version for this
        organization is its own audience, so a newer public price does not supersede it — and
        another organization's bespoke version is still a plain 404, never `not_offerable`."""
        old, _yearly, _monthly = _repriced(catalog)
        ours = catalog.add(
            make_version(
                product_key=PRODUCT,
                currency="INR",
                amount_minor=150000,
                plan_id=old.plan_id,
                version_no=4,
                restricted_to_org_id=org_id,
            )
        )
        theirs = catalog.add(
            make_version(
                product_key=PRODUCT,
                currency="INR",
                amount_minor=100000,
                plan_id=old.plan_id,
                version_no=5,
                restricted_to_org_id=other_org,
            )
        )
        _as(org_id)
        refused = await client.post(
            "/api/v1/subscriptions",
            json={"product_key": PRODUCT, "plan_version_id": str(theirs.id), "trial_days": 14},
        )
        assert refused.status_code == 404, refused.text
        assert _code(refused) == domain.CODE_NOT_FOUND

        response = await client.post(
            "/api/v1/subscriptions",
            json={"product_key": PRODUCT, "plan_version_id": str(ours.id), "trial_days": 14},
        )
        assert response.status_code == 201, response.text
        sub = await _read(session, uuid.UUID(response.json()["id"]))
        assert sub.plan_version_id == ours.id

    async def test_an_unreachable_catalog_refuses_rather_than_guesses(
        self, client: AsyncClient, catalog: FakeCatalog, org_id: uuid.UUID
    ) -> None:
        _old, _yearly, monthly = _repriced(catalog)
        catalog.offerable_down = True
        _as(org_id)
        response = await client.post(
            "/api/v1/subscriptions",
            json={"product_key": PRODUCT, "plan_version_id": str(monthly.id), "trial_days": 14},
        )
        assert response.status_code == 503, response.text
        listed = await client.get("/api/v1/subscriptions")
        assert listed.json() == [], "nothing was created"

    async def test_staff_add_product_accepts_the_yearly_version(
        self,
        client: AsyncClient,
        session: AsyncSession,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
    ) -> None:
        _old, yearly, _monthly = _repriced(catalog)
        response = await client.post(
            "/internal/v1/subscriptions",
            json={
                "organization_id": str(org_id),
                "product_key": PRODUCT,
                "plan_version_id": str(yearly.id),
                "trial": True,
                "operator_id": OPERATOR,
            },
            headers=_internal(),
        )
        assert response.status_code == 201, response.text
        sub = await _read(session, uuid.UUID(response.json()["id"]))
        assert (sub.state, sub.plan_version_id, sub.billing_interval) == (
            STATE_TRIAL,
            yearly.id,
            "year",
        )


# ---------------------------------------------------------------------- the billing run


async def test_a_yearly_subscription_is_billed_one_period_a_year(
    run: BillingRunSweep, billing: FakeBilling, session: AsyncSession, org_id: uuid.UUID
) -> None:
    start = datetime(2026, 10, 6, 10, 0, tzinfo=UTC)
    year = timedelta(days=365)
    subscription_id = uuid.uuid4()
    async with session.begin():
        session.add(
            Subscription(
                id=subscription_id,
                organization_id=org_id,
                product_key=PRODUCT,
                plan_version_id=uuid.uuid4(),
                state=STATE_ACTIVE,
                current_period_start=start - year,
                current_period_end=start,
                billing_interval="year",
                currency="INR",
            )
        )
        await session.flush()
        session.add(
            SubscriptionItem(
                organization_id=org_id,
                subscription_id=subscription_id,
                price_id=uuid.uuid4(),
                quantity=1,
            )
        )

    await run.tick(now=start)
    (renewal,) = billing.requests
    assert (renewal.period_start, renewal.period_end) == (start, start + year)

    # A pass every month of the year raises nothing more.
    for month in range(1, 13):
        await run.tick(now=start + timedelta(days=30 * month))
        assert len(billing.requests) == 1, f"month {month}"

    await run.tick(now=start + year)
    assert [(r.period_start, r.period_end) for r in billing.requests] == [
        (start, start + year),
        (start + year, start + 2 * year),
    ]


async def test_the_cadence_refusal_is_the_write_rule_not_a_picker_hint() -> None:
    """The rule both writes apply, stated directly: one cadence for the life of a subscription."""
    with pytest.raises(domain.CadenceChangeRefused):
        domain.assert_same_cadence("month", "year")
    with pytest.raises(domain.CadenceChangeRefused):
        domain.assert_same_cadence("year", "month")
    domain.assert_same_cadence("month", "month")
