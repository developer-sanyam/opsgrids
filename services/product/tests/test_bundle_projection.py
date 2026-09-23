"""A bundle entitles its members, and an activation is on while any of its sources is.

Before this, a `suite/trio` subscription projected one activation — for `suite`, a key no gateway
route checks — and the customer who bought three products was entitled to none. The fix has two
halves here, and both are proven against real PostgreSQL through the real consumer:

* **fan-out** — one subscription event speaks for every product in `entitled_products`, and a
  plan change diffs the old member set against the new one;
* **OR of sources** — the same product can be entitled by its own subscription *and* a bundle, so
  an activation's status is recomputed from `activation_sources` rather than written by whichever
  event arrived last. Cancelling the suite must not switch off the standalone HRMS the customer
  still pays for.

And the properties every projection here already carried, re-proven for the new shape: the
ordering guard (now per source), idempotent redelivery, the tenant GUC being load-bearing, and
one organization never seeing another's rows.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import selectors, services
from app.config import settings
from app.consumers import project_subscription_event
from app.main import app
from app.models import ActivationSource
from platform_core.auth import Principal, Scope, ScopeContext, get_principal
from platform_core.db import dispose_engine, init_engine
from platform_core.errors import BusinessRuleProblem
from platform_core.internal_auth import mint_service_token

pytestmark = pytest.mark.integration

DUO = ["hrms", "crm"]
TRIO = ["hrms", "crm", "finance"]
T0 = datetime(2026, 9, 1, tzinfo=UTC)


@pytest.fixture(autouse=True)
async def _engine(launched: tuple[str, ...]) -> Any:
    """The consumer opens its own `session_scope`, which needs the process engine. Every test
    here sells the trio, so its members are launched (see conftest `launched`)."""
    init_engine(settings())
    yield
    await dispose_engine()


def _event(
    org_id: uuid.UUID,
    *,
    state: str,
    product_key: str = "suite",
    members: list[str] | None = None,
    minutes: int = 0,
    event_type: str | None = None,
    subscription_id: uuid.UUID | None = None,
    trial_ends_at: datetime | None = None,
    event_id: uuid.UUID | None = None,
    with_members: bool = True,
) -> dict[str, Any]:
    """One `commerce.subscription.*` envelope, as subscription-service now emits it."""
    payload: dict[str, Any] = {
        "organization_id": str(org_id),
        "product_key": product_key,
        "state": state,
        "trial_ends_at": trial_ends_at.isoformat() if trial_ends_at else None,
    }
    if with_members:
        payload["entitled_products"] = members if members is not None else [product_key]
    if subscription_id is not None:
        payload["subscription_id"] = str(subscription_id)
    return {
        "event_id": str(event_id or uuid.uuid4()),
        "event_type": event_type or f"commerce.subscription.{state}",
        "organization_id": str(org_id),
        "aggregate_type": "subscription",
        "aggregate_id": str(subscription_id or uuid.uuid4()),
        "occurred_at": (T0 + timedelta(minutes=minutes)).isoformat(),
        "payload": payload,
    }


async def _entitled(session: AsyncSession, org_id: uuid.UUID) -> set[str]:
    async with session.begin():
        session.expire_all()
        keys = await selectors.entitlements_for(session, org_id)
    return set(keys) - {"core", "notification"}


async def _sources(session: AsyncSession, org_id: uuid.UUID) -> dict[tuple[str, str], str]:
    async with session.begin():
        session.expire_all()
        rows = (
            (
                await session.execute(
                    select(ActivationSource).where(ActivationSource.organization_id == org_id)
                )
            )
            .scalars()
            .all()
        )
    return {(r.product_key, r.source_product_key): r.status for r in rows}


def _ctx(org_id: uuid.UUID) -> ScopeContext:
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


class TestFanOut:
    async def test_a_trio_activates_its_three_members_and_not_itself(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        await project_subscription_event(_event(org_id, state="active", members=TRIO))
        assert await _entitled(session, org_id) == set(TRIO)
        async with session.begin():
            assert await selectors.get_activation(session, org_id, "suite") is None

    async def test_a_trial_bundle_entitles_every_member_until_it_lapses(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        ends = datetime.now(UTC) + timedelta(days=14)
        await project_subscription_event(
            _event(org_id, state="trial", members=DUO, trial_ends_at=ends)
        )
        assert await _entitled(session, org_id) == set(DUO)
        async with session.begin():
            hrms = await selectors.get_activation(session, org_id, "hrms")
        assert hrms is not None and hrms.status == "trial" and hrms.trial_ends_at == ends

    async def test_a_standalone_event_without_members_still_folds_onto_its_product(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """Additive contract: an event from before `entitled_products` means `[product_key]`."""
        await project_subscription_event(
            _event(org_id, state="active", product_key="hrms", with_members=False)
        )
        assert await _entitled(session, org_id) == {"hrms"}

    async def test_a_bundle_event_without_members_projects_nothing(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """The old fold would have activated `suite` — which routes nowhere. It is recorded and
        left for the event that names the members instead."""
        await project_subscription_event(_event(org_id, state="active", with_members=False))
        assert await _entitled(session, org_id) == set()
        assert await _sources(session, org_id) == {}


class TestWhatAMemberListMayName:
    async def test_a_soon_member_is_never_projected(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """`projects` is seeded `soon`: selling it would sell a customer 404s."""
        await project_subscription_event(
            _event(org_id, state="active", members=[*TRIO, "projects"])
        )
        assert await _entitled(session, org_id) == set(TRIO)
        assert ("projects", "suite") not in await _sources(session, org_id)

    async def test_platform_and_bundle_members_are_refused(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        await project_subscription_event(
            _event(org_id, state="active", members=["hrms", "core", "suite"])
        )
        assert set(await _sources(session, org_id)) == {("hrms", "suite")}

    async def test_a_plain_products_event_speaks_only_for_itself(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """A member list is honoured only on a bundle product's subscription."""
        await project_subscription_event(
            _event(org_id, state="active", product_key="crm", members=["crm", "hrms", "finance"])
        )
        assert await _entitled(session, org_id) == {"crm"}


class TestOrOfSources:
    async def test_suspending_the_suite_de_entitles_only_members_nothing_else_feeds(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        await project_subscription_event(
            _event(org_id, state="active", product_key="crm", minutes=0)
        )
        await project_subscription_event(_event(org_id, state="active", members=TRIO, minutes=1))
        assert await _entitled(session, org_id) == set(TRIO)

        await project_subscription_event(_event(org_id, state="suspended", members=TRIO, minutes=2))
        assert await _entitled(session, org_id) == {"crm"}, (
            "crm has its own live subscription and must survive the suite's suspension"
        )
        sources = await _sources(session, org_id)
        assert sources[("crm", "crm")] == "active"
        assert sources[("crm", "suite")] == "suspended"

    async def test_standalone_plus_suite_then_cancel_suite_keeps_the_standalone(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        await project_subscription_event(_event(org_id, state="active", members=TRIO, minutes=0))
        await project_subscription_event(
            _event(org_id, state="active", product_key="hrms", minutes=1)
        )
        await project_subscription_event(_event(org_id, state="cancelled", members=TRIO, minutes=2))
        assert await _entitled(session, org_id) == {"hrms"}

        # And cancelling the standalone too finally switches it off.
        await project_subscription_event(
            _event(org_id, state="cancelled", product_key="hrms", minutes=3)
        )
        assert await _entitled(session, org_id) == set()
        async with session.begin():
            hrms = await selectors.get_activation(session, org_id, "hrms")
        assert hrms is not None and hrms.deactivated_at is not None

    async def test_a_suite_in_grace_keeps_every_member(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """`past_due` entitles (ADR-0005 §2) — for every member, not only the first."""
        await project_subscription_event(_event(org_id, state="active", members=TRIO, minutes=0))
        await project_subscription_event(_event(org_id, state="past_due", members=TRIO, minutes=1))
        assert await _entitled(session, org_id) == set(TRIO)

    async def test_a_hand_activation_is_the_products_own_source(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """The manual path and the projection feed the same OR: a suite cancellation must not
        switch off a product a human activated by hand."""
        async with session.begin():
            await services.activate(session, _ctx(org_id), product_key="engage")
        await project_subscription_event(
            _event(org_id, state="active", members=["engage", "crm"], minutes=0)
        )
        await project_subscription_event(
            _event(org_id, state="cancelled", members=["engage", "crm"], minutes=1)
        )
        assert await _entitled(session, org_id) == {"engage"}


class TestPlanChanges:
    async def test_duo_to_trio_adds_and_trio_to_duo_removes(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        await project_subscription_event(_event(org_id, state="active", members=DUO, minutes=0))
        assert await _entitled(session, org_id) == set(DUO)

        await project_subscription_event(
            _event(
                org_id,
                state="active",
                members=TRIO,
                minutes=1,
                event_type="commerce.subscription.upgraded",
            )
        )
        assert await _entitled(session, org_id) == set(TRIO)

        await project_subscription_event(
            _event(
                org_id,
                state="active",
                members=DUO,
                minutes=2,
                event_type="commerce.subscription.downgraded",
            )
        )
        assert await _entitled(session, org_id) == set(DUO)
        assert (await _sources(session, org_id))[("finance", "suite")] == "removed"

    async def test_a_downgrade_keeps_a_removed_member_another_source_feeds(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        await project_subscription_event(
            _event(org_id, state="active", product_key="finance", minutes=0)
        )
        await project_subscription_event(_event(org_id, state="active", members=TRIO, minutes=1))
        await project_subscription_event(_event(org_id, state="active", members=DUO, minutes=2))
        assert await _entitled(session, org_id) == set(TRIO)

    async def test_the_dead_suite_activation_from_before_the_fix_is_retired(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """An organization that bought a suite under the old fold has an activation for `suite`
        and, after the migration's backfill, a `('suite', 'suite')` source. The first event that
        names the members is a member set without `suite` in it, so the diff retires it."""
        await project_subscription_event(
            _event(org_id, state="active", product_key="suite", members=["suite"], minutes=0)
        )
        await project_subscription_event(_event(org_id, state="active", members=TRIO, minutes=1))
        assert await _entitled(session, org_id) == set(TRIO)
        assert (await _sources(session, org_id))[("suite", "suite")] == "removed"


class TestOrderingAndRedelivery:
    async def test_an_older_upgrade_arriving_after_a_newer_downgrade_is_dropped(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """duo (t0) → trio (t1) → duo (t2), delivered t0, t2, t1. The stale trio must not
        re-add finance — the tombstone's timestamp is what drops it."""
        await project_subscription_event(_event(org_id, state="active", members=DUO, minutes=0))
        await project_subscription_event(_event(org_id, state="active", members=TRIO, minutes=1))
        await project_subscription_event(_event(org_id, state="active", members=DUO, minutes=3))
        await project_subscription_event(_event(org_id, state="active", members=TRIO, minutes=2))
        assert await _entitled(session, org_id) == set(DUO)

    async def test_a_reversed_suspend_reactivate_leaves_the_members_on(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        await project_subscription_event(_event(org_id, state="active", members=TRIO, minutes=5))
        await project_subscription_event(_event(org_id, state="suspended", members=TRIO, minutes=3))
        assert await _entitled(session, org_id) == set(TRIO)

    async def test_the_guard_is_per_source_not_per_activation(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """A standalone cancellation at t1 must apply even though a suite event at t2 already
        touched the same activation — the two are independent histories."""
        await project_subscription_event(
            _event(org_id, state="active", product_key="hrms", minutes=0)
        )
        await project_subscription_event(_event(org_id, state="active", members=DUO, minutes=2))
        await project_subscription_event(
            _event(org_id, state="cancelled", product_key="hrms", minutes=1)
        )
        sources = await _sources(session, org_id)
        assert sources[("hrms", "hrms")] == "suspended"
        assert sources[("hrms", "suite")] == "active"
        assert await _entitled(session, org_id) == set(DUO)

    async def test_redelivery_is_a_no_op(self, session: AsyncSession, org_id: uuid.UUID) -> None:
        event_id = uuid.uuid4()
        first = _event(org_id, state="active", members=TRIO, event_id=event_id)
        await project_subscription_event(first)
        before = await _sources(session, org_id)

        # Same id, contradictory body — dropped as already processed.
        again = _event(org_id, state="suspended", members=DUO, event_id=event_id, minutes=9)
        await project_subscription_event(again)
        assert await _sources(session, org_id) == before
        assert await _entitled(session, org_id) == set(TRIO)

    async def test_the_subscription_id_is_recorded_on_every_member(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        sub = uuid.uuid4()
        await project_subscription_event(
            _event(org_id, state="active", members=TRIO, subscription_id=sub)
        )
        async with session.begin():
            rows = await selectors.sources_from(session, org_id, "suite")
        assert {r.source_subscription_id for r in rows} == {sub}


class TestTenancy:
    async def test_another_organizations_bundle_is_invisible(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        stranger = uuid.uuid4()
        await project_subscription_event(_event(stranger, state="active", members=TRIO))
        assert await _entitled(session, org_id) == set()
        assert await _sources(session, org_id) == {}, "RLS must hide another tenant's sources"

    async def test_a_source_write_fails_without_the_tenant_guc(
        self, unbound_app_session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        with pytest.raises(Exception, match="row-level security"):
            async with unbound_app_session.begin():
                await services.project_subscription(
                    unbound_app_session,
                    organization_id=org_id,
                    product_key="suite",
                    state="active",
                    trial_ends_at=None,
                    entitled_products=TRIO,
                )


class TestManualPaths:
    async def test_a_bundle_cannot_be_activated_by_hand(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        with pytest.raises(BusinessRuleProblem, match="bundle"):
            async with session.begin():
                await services.activate(session, _ctx(org_id), product_key="suite")

    async def test_deactivating_a_member_the_suite_still_feeds_is_refused(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        await project_subscription_event(_event(org_id, state="active", members=TRIO))
        with pytest.raises(BusinessRuleProblem, match="included in your suite"):
            async with session.begin():
                await services.deactivate(session, _ctx(org_id), product_key="hrms")
        assert "hrms" in await _entitled(session, org_id)

    async def test_the_suite_is_registered_as_a_bundle(self, session: AsyncSession) -> None:
        async with session.begin():
            suite = await selectors.get_product(session, "suite")
        assert suite is not None and suite.is_bundle and not suite.is_platform


class TestReads:
    """`entitled_by` on the activation list, and the internal registry read."""

    @staticmethod
    def _as(org_id: uuid.UUID, perms: list[str]) -> None:
        app.dependency_overrides[get_principal] = lambda: Principal(
            sub=uuid.uuid4(),
            organization_id=org_id,
            membership_id=uuid.uuid4(),
            perms=perms,
            entitlements=["core"],
            audience="app",
        )

    async def test_the_activation_list_says_what_entitles_each_product(
        self, client: AsyncClient, org_id: uuid.UUID
    ) -> None:
        await project_subscription_event(
            _event(org_id, state="active", product_key="crm", minutes=0)
        )
        await project_subscription_event(_event(org_id, state="active", members=DUO, minutes=1))
        self._as(org_id, ["core.product.activation.read:ORG"])
        response = await client.get("/api/v1/products/activations")
        assert response.status_code == 200, response.text
        by_key = {a["product_key"]: a["entitled_by"] for a in response.json()}
        assert by_key == {"crm": ["crm", "suite"], "hrms": ["suite"]}

    async def test_the_activation_list_needs_the_read_permission(
        self, client: AsyncClient, org_id: uuid.UUID
    ) -> None:
        self._as(org_id, [])
        response = await client.get("/api/v1/products/activations")
        assert response.status_code == 403

    async def test_the_activation_list_never_shows_another_tenant(
        self, client: AsyncClient, org_id: uuid.UUID
    ) -> None:
        stranger = uuid.uuid4()
        await project_subscription_event(_event(stranger, state="active", members=TRIO))
        self._as(org_id, ["core.product.activation.read:ORG"])
        response = await client.get("/api/v1/products/activations")
        assert response.status_code == 200
        assert response.json() == []

    async def test_the_registry_read_needs_a_service_token(self, client: AsyncClient) -> None:
        assert (await client.get("/internal/v1/products")).status_code in (401, 403)

    async def test_the_registry_read_flags_the_bundle_and_the_platform(
        self, client: AsyncClient
    ) -> None:
        headers = {
            "X-Internal-Token": mint_service_token(settings(), audience="product", caller="tests"),
            "X-Calling-Service": "tests",
        }
        response = await client.get("/internal/v1/products", headers=headers)
        assert response.status_code == 200
        registry = {p["key"]: p for p in response.json()["products"]}
        assert registry["suite"]["is_bundle"] is True
        assert registry["core"]["is_platform"] is True
        assert registry["hrms"]["is_bundle"] is False


class TestTheMigrationBackfill:
    """Migration b3d5f7a9c1e2 gives every pre-existing activation one source — its own — so the
    first recompute after deploy lands each one exactly where it already was."""

    @staticmethod
    def _statement() -> str:
        import importlib.util
        from pathlib import Path

        path = (
            Path(__file__).resolve().parent.parent
            / "migrations"
            / "versions"
            / "b3d5f7a9c1e2_activation_sources_and_bundles.py"
        )
        spec = importlib.util.spec_from_file_location("_backfill_migration", path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return str(module.BACKFILL_SOURCES)

    async def test_each_activation_gets_its_own_source_at_its_own_state(
        self, session: AsyncSession, admin_session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        from sqlalchemy import text

        from app.models import Activation

        ends = datetime.now(UTC) + timedelta(days=3)
        async with admin_session.begin():
            admin_session.add_all(
                [
                    Activation(organization_id=org_id, product_key="hrms", status="active"),
                    Activation(
                        organization_id=org_id,
                        product_key="crm",
                        status="trial",
                        trial_ends_at=ends,
                        state_occurred_at=T0,
                    ),
                    Activation(
                        organization_id=org_id,
                        product_key="finance",
                        status="active",
                        deactivated_at=T0,
                    ),
                ]
            )
        statement = self._statement()
        async with admin_session.begin():
            await admin_session.execute(text(statement))
            await admin_session.execute(text(statement))  # idempotent

        assert await _sources(session, org_id) == {
            ("hrms", "hrms"): "active",
            ("crm", "crm"): "trial",
            ("finance", "finance"): "suspended",
        }

        # The first event after deploy recomputes from those sources and changes nothing for
        # the standalone product: a later standalone cancel still switches hrms off.
        await project_subscription_event(
            _event(org_id, state="cancelled", product_key="hrms", minutes=1)
        )
        assert await _entitled(session, org_id) == {"crm"}
