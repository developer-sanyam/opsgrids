"""The load-bearing behaviours of the catalog service, against real PostgreSQL.

Three properties carry the weight, and each has a negative case:

1. **Bespoke visibility.** A version restricted to one organization is returned to that
   organization and to nobody else. This is the filter that stands in for row-level security
   on a global table, so proving org B *cannot* see org A's private price is the whole point.
2. **Published immutability.** Once published, a version cannot be changed — refused at the
   service layer with a usable message, and by a database trigger underneath, so no code path
   can quietly re-price a version somebody is pinned to.
3. **The operator audience boundary.** Authoring is refused with a 404 — not a 403 — to any
   token that did not arrive through the operator gateway, so the surface's existence is not
   confirmed to a customer token.
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app import selectors, services
from app.main import app
from app.models import OutboxEvent, PlanVersion
from app.schemas import FeatureIn, PlanCreateIn, PriceIn, VersionCreateIn
from platform_core.auth import Principal, get_principal
from platform_core.errors import BusinessRuleProblem
from tests.helpers import customer_principal, operator_principal

pytestmark = pytest.mark.integration


@contextlib.contextmanager
def as_principal(principal: Principal) -> Iterator[None]:
    app.dependency_overrides[get_principal] = lambda: principal
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_principal, None)


async def _make_plan(
    session: AsyncSession, *, product_key: str, code: str = "std", name: str = "Standard"
):
    op = operator_principal()
    return await services.create_plan(
        session, op, PlanCreateIn(product_key=product_key, code=code, name=name), request=None
    )


async def _make_published_version(
    session: AsyncSession,
    plan,
    *,
    currency: str = "USD",
    amount_minor: int = 1000,
    restricted_to_org_id: uuid.UUID | None = None,
    features: list[FeatureIn] | None = None,
) -> PlanVersion:
    op = operator_principal()
    version = await services.create_version(
        session,
        op,
        plan,
        VersionCreateIn(
            currency=currency,
            restricted_to_org_id=restricted_to_org_id,
            prices=[PriceIn(kind="flat", amount_minor=amount_minor, currency=currency)],
            features=features or [],
        ),
        request=None,
    )
    return await services.publish_version(session, op, version, request=None)


# --------------------------------------------------------------------- bespoke visibility


class TestBespokeVisibility:
    async def test_a_bespoke_version_is_hidden_from_other_organizations(
        self, platform_session: AsyncSession, org_a: uuid.UUID, org_b: uuid.UUID
    ) -> None:
        """The negative-authz test that replaces RLS on a global table.

        A public version is seen by both organizations; a version restricted to org A is seen
        by org A and never by org B. A leak here would show one customer another's negotiated
        enterprise price.
        """
        async with platform_session.begin():
            plan = await _make_plan(platform_session, product_key="widgets")
            public = await _make_published_version(platform_session, plan, amount_minor=1000)
            bespoke = await _make_published_version(
                platform_session, plan, amount_minor=500, restricted_to_org_id=org_a
            )

        async with platform_session.begin():
            seen_by_a = await selectors.visible_plans(
                platform_session, product_key="widgets", org_id=org_a
            )
            seen_by_b = await selectors.visible_plans(
                platform_session, product_key="widgets", org_id=org_b
            )

        a_versions = {v.id for _, versions in seen_by_a for v in versions}
        b_versions = {v.id for _, versions in seen_by_b for v in versions}

        assert public.id in a_versions
        assert public.id in b_versions, "a public version must be visible to every customer"
        assert bespoke.id in a_versions, "the audience-of-one must see its own version"
        assert bespoke.id not in b_versions, (
            "a bespoke version leaked to an organization it was not negotiated for"
        )

    async def test_a_draft_version_is_never_visible_to_a_customer(
        self, platform_session: AsyncSession, org_a: uuid.UUID
    ) -> None:
        """Only published versions reach a customer — a draft price is not an offer yet."""
        op = operator_principal()
        async with platform_session.begin():
            plan = await _make_plan(platform_session, product_key="gadgets")
            await services.create_version(
                platform_session,
                op,
                plan,
                VersionCreateIn(
                    currency="USD",
                    prices=[PriceIn(kind="flat", amount_minor=999, currency="USD")],
                ),
                request=None,
            )

        async with platform_session.begin():
            pairs = await selectors.visible_plans(
                platform_session, product_key="gadgets", org_id=org_a
            )
        versions = [v for _, vs in pairs for v in vs]
        assert versions == []

    async def test_the_customer_http_read_applies_the_filter(
        self, client, platform_session: AsyncSession, org_a: uuid.UUID, org_b: uuid.UUID
    ) -> None:
        """End to end over HTTP: the same filter, through the customer route and its permission."""
        async with platform_session.begin():
            plan = await _make_plan(platform_session, product_key="doohickeys")
            await _make_published_version(
                platform_session, plan, amount_minor=700, restricted_to_org_id=org_a
            )

        with as_principal(customer_principal(org_a)):
            resp_a = await client.get("/api/v1/plans", params={"product_key": "doohickeys"})
        with as_principal(customer_principal(org_b)):
            resp_b = await client.get("/api/v1/plans", params={"product_key": "doohickeys"})

        assert resp_a.status_code == 200
        assert resp_b.status_code == 200
        a_versions = [v for p in resp_a.json() for v in p["versions"]]
        b_versions = [v for p in resp_b.json() for v in p["versions"]]
        assert len(a_versions) == 1, "org A negotiated this version and must see it"
        assert b_versions == [], "org B must not see another org's bespoke version"

    async def test_a_customer_without_the_permission_is_forbidden(
        self, client, org_a: uuid.UUID
    ) -> None:
        with as_principal(customer_principal(org_a, perms=[])):
            resp = await client.get("/api/v1/plans", params={"product_key": "widgets"})
        assert resp.status_code == 403


# --------------------------------------------------------------------- immutability


class TestPublishedImmutability:
    async def test_the_service_layer_refuses_to_republish(
        self, platform_session: AsyncSession
    ) -> None:
        """The service guard: a published version is not a draft, so it cannot be touched."""
        async with platform_session.begin():
            plan = await _make_plan(platform_session, product_key="frozen")
            version = await _make_published_version(platform_session, plan)

        with pytest.raises(BusinessRuleProblem, match="published"):
            async with platform_session.begin():
                fresh = await services.require_version(platform_session, version.id)
                await services.publish_version(
                    platform_session, operator_principal(), fresh, request=None
                )

    async def test_the_database_trigger_refuses_a_raw_update(
        self, platform_session: AsyncSession
    ) -> None:
        """The net under the guard: even a direct UPDATE, bypassing every service check, raises."""
        async with platform_session.begin():
            plan = await _make_plan(platform_session, product_key="frozen2")
            version = await _make_published_version(platform_session, plan)

        with pytest.raises(IntegrityError, match="immutable"):
            async with platform_session.begin():
                await platform_session.execute(
                    text(
                        "UPDATE catalog.plan_versions SET billing_interval = 'year' "
                        "WHERE id = CAST(:id AS uuid)"
                    ),
                    {"id": str(version.id)},
                )

    async def test_a_draft_can_still_be_published(self, platform_session: AsyncSession) -> None:
        """The transition the trigger must *allow*: draft -> published, exactly once."""
        async with platform_session.begin():
            plan = await _make_plan(platform_session, product_key="thaws")
            version = await _make_published_version(platform_session, plan)
        assert version.state == "published"
        assert version.published_at is not None


# --------------------------------------------------------------------- frozen price sheet


class TestFrozenPriceSheet:
    """The triggers of migration e3f8a91c7b2d: a published version's *children* are frozen too.

    The version-row trigger above never covered the money — `prices.amount_minor` and the
    entitlements in `features` lived on unguarded child tables, and `plans → plan_versions →
    prices/features` is an ON DELETE CASCADE chain. These tests raw-UPDATE and raw-DELETE below
    the service layer, mirroring `test_the_database_trigger_refuses_a_raw_update`, and prove the
    one mutation the clone flow depends on — editing a *draft*'s sheet — still works.
    """

    async def test_a_raw_update_of_a_published_price_is_refused(
        self, platform_session: AsyncSession
    ) -> None:
        """Re-pricing a published version by editing its price row directly must raise."""
        async with platform_session.begin():
            plan = await _make_plan(platform_session, product_key="sheet-upd")
            version = await _make_published_version(platform_session, plan, amount_minor=1000)

        with pytest.raises(IntegrityError, match="immutable"):
            async with platform_session.begin():
                await platform_session.execute(
                    text(
                        "UPDATE catalog.prices SET amount_minor = 1 "
                        "WHERE plan_version_id = CAST(:id AS uuid)"
                    ),
                    {"id": str(version.id)},
                )

    async def test_a_raw_delete_of_a_published_price_is_refused(
        self, platform_session: AsyncSession
    ) -> None:
        async with platform_session.begin():
            plan = await _make_plan(platform_session, product_key="sheet-del")
            version = await _make_published_version(platform_session, plan)

        with pytest.raises(IntegrityError, match="immutable"):
            async with platform_session.begin():
                await platform_session.execute(
                    text("DELETE FROM catalog.prices WHERE plan_version_id = CAST(:id AS uuid)"),
                    {"id": str(version.id)},
                )

    async def test_a_raw_delete_of_a_published_feature_is_refused(
        self, platform_session: AsyncSession
    ) -> None:
        """The entitlements are part of what the customer signed under, same rule as the price."""
        async with platform_session.begin():
            plan = await _make_plan(platform_session, product_key="sheet-feat")
            version = await _make_published_version(
                platform_session, plan, features=[FeatureIn(feature_key="seats", value=10)]
            )

        with pytest.raises(IntegrityError, match="immutable"):
            async with platform_session.begin():
                await platform_session.execute(
                    text("DELETE FROM catalog.features WHERE plan_version_id = CAST(:id AS uuid)"),
                    {"id": str(version.id)},
                )

    async def test_a_published_version_row_cannot_be_deleted(
        self, platform_session: AsyncSession
    ) -> None:
        """UPDATE was already guarded; DELETE was the hole. A pinned sheet must not vanish."""
        async with platform_session.begin():
            plan = await _make_plan(platform_session, product_key="sheet-vdel")
            version = await _make_published_version(platform_session, plan)

        with pytest.raises(IntegrityError, match="retire the plan"):
            async with platform_session.begin():
                await platform_session.execute(
                    text("DELETE FROM catalog.plan_versions WHERE id = CAST(:id AS uuid)"),
                    {"id": str(version.id)},
                )

    async def test_deleting_the_parent_plan_cannot_cascade_over_a_published_version(
        self, platform_session: AsyncSession
    ) -> None:
        """One DELETE on `plans` used to cascade a customer's whole price sheet into nothing —
        the version trigger now stops the cascade itself."""
        async with platform_session.begin():
            plan = await _make_plan(platform_session, product_key="sheet-pdel")
            await _make_published_version(platform_session, plan)

        with pytest.raises(IntegrityError, match="retire the plan"):
            async with platform_session.begin():
                await platform_session.execute(
                    text("DELETE FROM catalog.plans WHERE id = CAST(:id AS uuid)"),
                    {"id": str(plan.id)},
                )

    async def test_a_draft_price_sheet_stays_freely_mutable(
        self, platform_session: AsyncSession
    ) -> None:
        """The mutation the triggers must *allow*: a draft's children are working material —
        `create_version`'s clone flow edits and replaces them before publish."""
        async with platform_session.begin():
            plan = await _make_plan(platform_session, product_key="sheet-draft")
            draft = await services.create_version(
                platform_session,
                operator_principal(),
                plan,
                VersionCreateIn(
                    currency="USD",
                    prices=[PriceIn(kind="flat", amount_minor=1000, currency="USD")],
                ),
                request=None,
            )

        async with platform_session.begin():
            await platform_session.execute(
                text(
                    "UPDATE catalog.prices SET amount_minor = 2500 "
                    "WHERE plan_version_id = CAST(:id AS uuid)"
                ),
                {"id": str(draft.id)},
            )
            amount = (
                await platform_session.execute(
                    text(
                        "SELECT amount_minor FROM catalog.prices "
                        "WHERE plan_version_id = CAST(:id AS uuid)"
                    ),
                    {"id": str(draft.id)},
                )
            ).scalar_one()
            assert amount == 2500, "a draft price must accept a raw update"

            deleted = await platform_session.execute(
                text("DELETE FROM catalog.prices WHERE plan_version_id = CAST(:id AS uuid)"),
                {"id": str(draft.id)},
            )
            assert deleted.rowcount == 1, "a draft price must accept a raw delete"


# --------------------------------------------------------------------- clone-to-draft


class TestCloneToDraft:
    async def test_a_new_version_clones_the_previous_price_sheet(
        self, platform_session: AsyncSession
    ) -> None:
        """A version bump with no prices supplied inherits the predecessor's — never by
        mutating it, always as fresh rows on a new draft."""
        async with platform_session.begin():
            plan = await _make_plan(platform_session, product_key="cloner")
            v1 = await _make_published_version(
                platform_session,
                plan,
                amount_minor=1200,
                features=[FeatureIn(feature_key="seats", value=10)],
            )

        async with platform_session.begin():
            v2 = await services.create_version(
                platform_session,
                operator_principal(),
                plan,
                VersionCreateIn(currency="USD"),  # no prices/features -> clone
                request=None,
            )
            fresh = await selectors.get_version(platform_session, v2.id)

        assert fresh is not None
        assert fresh.version_no == v1.version_no + 1
        assert fresh.state == "draft"
        assert [(p.kind, p.amount_minor) for p in fresh.prices] == [("flat", 1200)]
        assert [(f.feature_key, f.value) for f in fresh.features] == [("seats", 10)]


# --------------------------------------------------------------------- operator boundary


class TestOperatorAudienceBoundary:
    async def test_an_operator_token_may_author(self, client) -> None:
        with as_principal(operator_principal(staff=True)):
            resp = await client.post(
                "/api/v1/admin/catalog/plans",
                json={"product_key": "hrms", "code": "authored", "name": "Authored"},
            )
        assert resp.status_code == 201
        assert resp.json()["code"] == "authored"

    async def test_a_product_audience_token_gets_404_not_403(self, client) -> None:
        """404, deliberately: a customer token learns nothing about whether this surface
        exists — the same rule the operator gateway enforces at the edge."""
        with as_principal(operator_principal(staff=False)):
            resp = await client.post(
                "/api/v1/admin/catalog/plans",
                json={"product_key": "hrms", "code": "sneaky", "name": "Sneaky"},
            )
        assert resp.status_code == 404

    async def test_an_operator_without_the_owner_role_gets_403_not_404(self, client) -> None:
        """The audience is right — the surface's existence is already conceded — so a staff
        member missing the `owner` role gets an ordinary Forbidden, per the ADR-0003 addendum's
        role matrix: catalog writes are `owner`-only."""
        with as_principal(operator_principal(roles=["billing_ops"])):
            resp = await client.post(
                "/api/v1/admin/catalog/plans",
                json={"product_key": "hrms", "code": "not-allowed", "name": "Not allowed"},
            )
        assert resp.status_code == 403

    async def test_an_unauthenticated_caller_is_rejected(self, client) -> None:
        resp = await client.post(
            "/api/v1/admin/catalog/plans",
            json={"product_key": "hrms", "code": "nope", "name": "Nope"},
        )
        assert resp.status_code == 401


# --------------------------------------------------------------------- events


class TestEvents:
    async def test_publishing_stages_a_published_event(
        self, platform_session: AsyncSession, admin_session: AsyncSession
    ) -> None:
        async with platform_session.begin():
            plan = await _make_plan(platform_session, product_key="eventful")
            version = await _make_published_version(platform_session, plan)

        # The outbox is policied to the platform sentinel; read it on the bypass session.
        count = (
            await admin_session.execute(
                select(func.count())
                .select_from(OutboxEvent)
                .where(
                    OutboxEvent.event_type == "catalog.plan_version.published",
                    OutboxEvent.aggregate_id == version.id,
                )
            )
        ).scalar_one()
        assert count == 1

    async def test_retiring_a_plan_stages_a_retired_event(
        self, platform_session: AsyncSession, admin_session: AsyncSession
    ) -> None:
        async with platform_session.begin():
            plan = await _make_plan(platform_session, product_key="retiring")
            await services.retire_plan(platform_session, operator_principal(), plan, request=None)

        count = (
            await admin_session.execute(
                select(func.count())
                .select_from(OutboxEvent)
                .where(
                    OutboxEvent.event_type == "catalog.plan.retired",
                    OutboxEvent.aggregate_id == plan.id,
                )
            )
        ).scalar_one()
        assert count == 1
