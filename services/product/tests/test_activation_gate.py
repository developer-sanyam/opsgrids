"""The hand-activation gate — a product a subscription manages is not the owner's to switch on.

`POST /products/activations` upserts the product's **own source**, the very row its standalone
subscription folds into. So a cancellation or a suspension could be undone by one request from the
org owner, and a cancelled subscription never emits again: nothing would ever switch the product
back off. The route now refuses (409 `subscription_managed`) once any source of the activation
carries subscription provenance — in any status, including the `suspended` a cancel leaves and the
`removed` tombstone a downgrade leaves — and still activates fresh and hand-only organizations.

Every state below is reached the way production reaches it: a real-shaped envelope, built by the
platform's own `build_envelope` from the payload subscription stages (`_lifecycle_payload`), through
the real consumer. The refusal is then asserted over HTTP, and asserted to have written nothing: no
outbox row, no audit row, no change to the activation.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import selectors
from app.config import settings
from app.consumers import project_subscription_event
from app.main import app
from app.models import SOURCE_ACTIVE, Activation, ActivationSource, AuditLog, OutboxEvent
from app.services import (
    CODE_SUBSCRIPTION_MANAGED,
    SUBSCRIPTION_MANAGED_DETAIL,
    SubscriptionManagedConflict,
)
from platform_core.auth import Principal, get_principal
from platform_core.db import dispose_engine, init_engine
from tests.helpers import T0, real_envelope

pytestmark = pytest.mark.integration

TRIO = ["hrms", "crm", "finance"]
DUO = ["hrms", "crm"]

#: The message the owner reads. Pinned verbatim: it is the product copy, and the Subscriptions
#: page it names is where the owner is sent.
MESSAGE = (
    "This product is managed by a subscription. Manage it on the Subscriptions page or contact "
    "OpsGrids."
)

WRITE = "core.product.activation.write"


@pytest.fixture(autouse=True)
async def _engine(launched: tuple[str, ...]) -> Any:
    """The consumer opens its own `session_scope`, which needs the process engine; the bundle
    cases sell the trio, so its members are launched (conftest `launched`)."""
    init_engine(settings())
    yield
    await dispose_engine()


def as_owner(org_id: uuid.UUID, perms: list[str] | None = None) -> None:
    app.dependency_overrides[get_principal] = lambda: Principal(
        sub=uuid.uuid4(),
        organization_id=org_id,
        membership_id=uuid.uuid4(),
        perms=perms if perms is not None else [f"{WRITE}:ORG", "core.product.activation.read:ORG"],
        entitlements=["core"],
        audience="app",
    )


async def _written(session: AsyncSession, org_id: uuid.UUID) -> dict[str, Any]:
    """Everything a refused request must not have changed: the activations, their sources, and the
    outbox and audit rows. Read on the tenant-bound session, filtered on the organization."""
    async with session.begin():
        session.expire_all()
        activations = (
            (await session.execute(select(Activation).where(Activation.organization_id == org_id)))
            .scalars()
            .all()
        )
        sources = (
            (
                await session.execute(
                    select(ActivationSource).where(ActivationSource.organization_id == org_id)
                )
            )
            .scalars()
            .all()
        )
        outbox = (
            (
                await session.execute(
                    select(OutboxEvent).where(OutboxEvent.organization_id == org_id)
                )
            )
            .scalars()
            .all()
        )
        audit = (
            (await session.execute(select(AuditLog).where(AuditLog.organization_id == org_id)))
            .scalars()
            .all()
        )
    return {
        "activations": sorted(
            (a.product_key, a.status, a.deactivated_at, a.trial_ends_at, a.state_occurred_at)
            for a in activations
        ),
        "sources": sorted(
            (
                s.product_key,
                s.source_product_key,
                s.status,
                s.source_subscription_id,
                s.state_occurred_at,
            )
            for s in sources
        ),
        "outbox": len(outbox),
        "audit": len(audit),
    }


async def _post(client: AsyncClient, product_key: str = "hrms", **body: Any) -> Any:
    return await client.post(
        "/api/v1/products/activations", json={"product_key": product_key, **body}
    )


def _assert_refused(response: Any) -> None:
    assert response.status_code == 409, response.text
    body = response.json()
    assert body["errors"][0]["code"] == "subscription_managed"
    assert body["errors"][0]["message"] == MESSAGE
    assert body["detail"] == MESSAGE
    assert body["title"] == "Conflict"
    assert body["type"].endswith("/conflict")


# ------------------------------------------------------------------ the predicate


class TestTheProvenancePredicate:
    """`ActivationSource.is_subscription_managed` — one property, one rule."""

    @pytest.mark.parametrize(
        ("sub_id", "occurred_at", "managed"),
        [
            (None, None, False),
            (uuid.uuid4(), None, True),
            (None, T0, True),
            (uuid.uuid4(), T0, True),
        ],
    )
    def test_either_column_is_provenance(
        self, sub_id: uuid.UUID | None, occurred_at: datetime | None, managed: bool
    ) -> None:
        source = ActivationSource(
            organization_id=uuid.uuid4(),
            product_key="hrms",
            source_product_key="hrms",
            status=SOURCE_ACTIVE,
            source_subscription_id=sub_id,
            state_occurred_at=occurred_at,
        )
        assert source.is_subscription_managed is managed

    @pytest.mark.parametrize("state", ["active", "past_due", "suspended", "cancelled", "trial"])
    async def test_a_real_event_leaves_provenance_in_every_state(
        self, session: AsyncSession, org_id: uuid.UUID, state: str
    ) -> None:
        """A real envelope, `occurred_at` included, folded by the real consumer: the row it writes
        is managed whatever state the event carried — a cancelled subscription is the case that
        matters, and it leaves the same mark a live one does."""
        envelope = real_envelope(org_id, state=state)
        assert envelope["occurred_at"], "the envelope carries the time the fold stamps"
        await project_subscription_event(envelope)
        async with session.begin():
            (source,) = await selectors.sources_for(session, org_id, "hrms")
        assert source.is_subscription_managed is True
        assert source.source_subscription_id == uuid.UUID(envelope["payload"]["subscription_id"])
        assert source.state_occurred_at == datetime.fromisoformat(envelope["occurred_at"])

    async def test_an_event_without_the_subscription_id_still_leaves_the_time(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """An event from before the payload named its subscription: only `occurred_at` is there,
        and it is enough."""
        envelope = real_envelope(org_id, state="cancelled", carry_subscription_id=False)
        envelope["aggregate_type"] = "not-a-subscription"  # nothing for `_subscription_id` to read
        await project_subscription_event(envelope)
        async with session.begin():
            (source,) = await selectors.sources_for(session, org_id, "hrms")
        assert source.source_subscription_id is None
        assert source.is_subscription_managed is True


# ------------------------------------------------------------------ the gate, over HTTP


class TestAfterASubscription:
    async def test_after_a_standalone_cancel_the_owner_cannot_switch_the_product_back_on(
        self, client: AsyncClient, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        sid = uuid.uuid4()
        await project_subscription_event(
            real_envelope(org_id, state="active", subscription_id=sid, minutes=0)
        )
        await project_subscription_event(
            real_envelope(org_id, state="cancelled", subscription_id=sid, minutes=1)
        )
        before = await _written(session, org_id)
        assert [a[:2] for a in before["activations"]] == [("hrms", "suspended")]

        as_owner(org_id)
        for body in ({}, {"trial_days": 365}):
            _assert_refused(await _post(client, **body))

        assert await _written(session, org_id) == before
        async with session.begin():
            keys = await selectors.entitlements_for(session, org_id)
        assert "hrms" not in keys

    async def test_after_a_suspension_the_owner_cannot_lift_it(
        self, client: AsyncClient, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        sid = uuid.uuid4()
        await project_subscription_event(
            real_envelope(org_id, state="active", subscription_id=sid, minutes=0)
        )
        await project_subscription_event(
            real_envelope(org_id, state="suspended", subscription_id=sid, minutes=1)
        )
        before = await _written(session, org_id)

        as_owner(org_id)
        _assert_refused(await _post(client))

        assert await _written(session, org_id) == before
        async with session.begin():
            keys = await selectors.entitlements_for(session, org_id)
        assert "hrms" not in keys

    async def test_after_a_suite_cancel_every_member_is_refused(
        self, client: AsyncClient, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """The member's only source is the suite's, a row of `source_product_key = 'suite'`: the
        gate reads every source of the activation, not just the product's own."""
        sid = uuid.uuid4()
        await project_subscription_event(
            real_envelope(
                org_id,
                state="active",
                product_key="suite",
                members=TRIO,
                subscription_id=sid,
                minutes=0,
            )
        )
        await project_subscription_event(
            real_envelope(
                org_id,
                state="cancelled",
                product_key="suite",
                members=TRIO,
                subscription_id=sid,
                minutes=1,
            )
        )
        before = await _written(session, org_id)

        as_owner(org_id)
        for member in TRIO:
            _assert_refused(await _post(client, member))

        assert await _written(session, org_id) == before

    async def test_after_a_downgrade_the_dropped_member_is_refused(
        self, client: AsyncClient, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """Trio to duo leaves `finance` as a `removed` tombstone of the suite. It is no longer
        sold to this customer, and it is not theirs to switch on by hand either."""
        sid = uuid.uuid4()
        await project_subscription_event(
            real_envelope(
                org_id,
                state="active",
                product_key="suite",
                members=TRIO,
                subscription_id=sid,
                minutes=0,
            )
        )
        await project_subscription_event(
            real_envelope(
                org_id,
                state="active",
                product_key="suite",
                members=DUO,
                subscription_id=sid,
                minutes=1,
                event_type="commerce.subscription.downgraded",
            )
        )
        before = await _written(session, org_id)
        assert ("finance", "suite", "removed") in {s[:3] for s in before["sources"]}

        as_owner(org_id)
        _assert_refused(await _post(client, "finance"))
        # And the members the duo still serves are managed too.
        _assert_refused(await _post(client, "hrms"))

        assert await _written(session, org_id) == before

    async def test_a_live_subscription_is_refused_too(
        self, client: AsyncClient, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """The refusal is about who manages the product, not about whether it is on: a live
        subscription would otherwise be re-stamped by a hand activation's own status and trial."""
        await project_subscription_event(real_envelope(org_id, state="active"))
        before = await _written(session, org_id)

        as_owner(org_id)
        _assert_refused(await _post(client, trial_days=1))

        assert await _written(session, org_id) == before

    async def test_the_standalone_cancel_is_refused_even_under_a_live_suite(
        self, client: AsyncClient, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """Both sources of one activation: the product's own cancelled one is what refuses, though
        the suite keeps the product on."""
        sid = uuid.uuid4()
        await project_subscription_event(real_envelope(org_id, state="cancelled", minutes=0))
        await project_subscription_event(
            real_envelope(
                org_id, state="active", product_key="suite", members=TRIO, subscription_id=sid
            )
        )
        before = await _written(session, org_id)

        as_owner(org_id)
        _assert_refused(await _post(client))

        assert await _written(session, org_id) == before


class TestAStopNamingAMemberTheSourceNeverFed:
    """A stop that applied a lapsed rung-up silently (ADR-0037) names the *new* pin's members.

    The suite held the duo, a deferred change to the trio came due, and the customer cancelled (or
    staff suspended) before the billing run claimed the boundary. The stop's payload names
    `finance`, which this subscription never entitled. A source row written for it would be a
    suspended activation with subscription provenance for a product the organization never had, and
    the gate would then refuse the owner a product no subscription ever fed.
    """

    @staticmethod
    async def _held_the_duo(org_id: uuid.UUID, sid: uuid.UUID, stop: str) -> None:
        await project_subscription_event(
            real_envelope(
                org_id,
                state="active",
                product_key="suite",
                members=DUO,
                subscription_id=sid,
                minutes=0,
            )
        )
        await project_subscription_event(
            real_envelope(
                org_id,
                state=stop,
                product_key="suite",
                members=TRIO,
                subscription_id=sid,
                minutes=1,
            )
        )

    @pytest.mark.parametrize("stop", ["cancelled", "suspended"])
    async def test_no_source_or_activation_is_made_for_the_member_never_held(
        self, session: AsyncSession, org_id: uuid.UUID, stop: str
    ) -> None:
        await self._held_the_duo(org_id, uuid.uuid4(), stop)

        written = await _written(session, org_id)
        assert [(a[0], a[1]) for a in written["activations"]] == [
            ("crm", "suspended"),
            ("hrms", "suspended"),
        ]
        assert {(s[0], s[1], s[2]) for s in written["sources"]} == {
            ("crm", "suite", "suspended"),
            ("hrms", "suite", "suspended"),
        }
        async with session.begin():
            entitled = set(await selectors.entitlements_for(session, org_id))
        assert not entitled & {"hrms", "crm", "finance"}

    @pytest.mark.parametrize("stop", ["cancelled", "suspended"])
    async def test_the_owner_may_still_activate_the_member_never_held(
        self, client: AsyncClient, org_id: uuid.UUID, stop: str
    ) -> None:
        await self._held_the_duo(org_id, uuid.uuid4(), stop)

        as_owner(org_id)
        response = await _post(client, "finance")
        assert response.status_code == 201, response.text
        assert response.json()["entitles"] is True
        # The members the duo did feed stay refused: the subscription still manages them.
        for member in DUO:
            _assert_refused(await _post(client, member))

    async def test_the_stop_still_carries_the_watermark_so_an_older_event_is_dropped(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """Skipping the new member must not weaken the ordering guard: the rows the source does
        have are stamped with the stop's time, so a late `upgraded` for the trio is stale."""
        sid = uuid.uuid4()
        await self._held_the_duo(org_id, sid, "cancelled")
        before = await _written(session, org_id)

        await project_subscription_event(
            real_envelope(
                org_id,
                state="active",
                product_key="suite",
                members=TRIO,
                subscription_id=sid,
                minutes=0,
                event_type="commerce.subscription.upgraded",
            )
        )

        assert await _written(session, org_id) == before

    async def test_a_member_the_subscription_did_hold_is_still_recorded_when_the_stop_names_it(
        self, client: AsyncClient, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """The rung-up was announced and folded before the stop, so the subscription did entitle
        `finance`: its row exists and the stop switches it off, and the owner is refused."""
        sid = uuid.uuid4()
        await project_subscription_event(
            real_envelope(
                org_id,
                state="active",
                product_key="suite",
                members=DUO,
                subscription_id=sid,
                minutes=0,
            )
        )
        await project_subscription_event(
            real_envelope(
                org_id,
                state="active",
                product_key="suite",
                members=TRIO,
                subscription_id=sid,
                minutes=1,
                event_type="commerce.subscription.upgraded",
            )
        )
        await project_subscription_event(
            real_envelope(
                org_id,
                state="cancelled",
                product_key="suite",
                members=TRIO,
                subscription_id=sid,
                minutes=2,
            )
        )

        written = await _written(session, org_id)
        assert ("finance", "suite", "suspended") in {s[:3] for s in written["sources"]}
        as_owner(org_id)
        _assert_refused(await _post(client, "finance"))

    async def test_a_stop_that_is_the_first_event_the_source_hears_writes_every_member(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """With no rows yet, the stop's own rows are the only watermark there will be, and nothing
        says which of its members were ever held, so all are written as before. A late `active`
        for the duo is then dropped as older than the stop."""
        sid = uuid.uuid4()
        await project_subscription_event(
            real_envelope(
                org_id,
                state="cancelled",
                product_key="suite",
                members=TRIO,
                subscription_id=sid,
                minutes=1,
            )
        )
        written = await _written(session, org_id)
        assert {s[:3] for s in written["sources"]} == {
            ("hrms", "suite", "suspended"),
            ("crm", "suite", "suspended"),
            ("finance", "suite", "suspended"),
        }

        await project_subscription_event(
            real_envelope(
                org_id,
                state="active",
                product_key="suite",
                members=DUO,
                subscription_id=sid,
                minutes=0,
            )
        )
        assert await _written(session, org_id) == written


class TestWithNoSubscription:
    async def test_a_fresh_organization_activates(
        self, client: AsyncClient, org_id: uuid.UUID
    ) -> None:
        as_owner(org_id)
        response = await _post(client)
        assert response.status_code == 201, response.text
        assert response.json()["product_key"] == "hrms"
        assert response.json()["status"] == "active"
        assert response.json()["entitles"] is True

    async def test_a_hand_only_organization_activates_deactivates_and_activates_again(
        self, client: AsyncClient, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """No provenance is ever written by a hand activation, so the gate never closes on one:
        a trial, a straight activation, a deactivation and a reactivation all go through."""
        as_owner(org_id)
        assert (await _post(client, trial_days=14)).status_code == 201
        assert (await _post(client)).status_code == 201
        assert (await client.delete("/api/v1/products/activations/hrms")).status_code == 200
        again = await _post(client, trial_days=14)
        assert again.status_code == 201, again.text
        assert again.json()["status"] == "trial"
        async with session.begin():
            (source,) = await selectors.sources_for(session, org_id, "hrms")
        assert source.is_subscription_managed is False

    async def test_a_subscription_for_a_different_product_does_not_refuse(
        self, client: AsyncClient, org_id: uuid.UUID
    ) -> None:
        """Provenance is per activation: a cancelled CRM leaves HRMS free to activate."""
        await project_subscription_event(
            real_envelope(org_id, state="cancelled", product_key="crm")
        )
        as_owner(org_id)
        assert (await _post(client, "hrms")).status_code == 201
        _assert_refused(await _post(client, "crm"))

    async def test_a_bundle_and_an_unreleased_product_keep_their_own_refusals(
        self, client: AsyncClient, org_id: uuid.UUID
    ) -> None:
        """The gate sits after the product's own rules: a bundle is still 422, an unknown key 404 —
        and neither is mistaken for a managed product."""
        as_owner(org_id)
        assert (await _post(client, "suite")).status_code == 422
        assert (await _post(client, "no-such-product")).status_code == 404


class TestTenancy:
    async def test_one_organizations_provenance_never_refuses_another(
        self, client: AsyncClient, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        stranger = uuid.uuid4()
        await project_subscription_event(real_envelope(stranger, state="cancelled"))
        await project_subscription_event(
            real_envelope(stranger, state="active", product_key="suite", members=TRIO, minutes=1)
        )

        as_owner(org_id)
        for member in TRIO:
            response = await _post(client, member)
            assert response.status_code == 201, (member, response.text)

        # And the other way round: the stranger is refused on its own provenance, untouched by ours.
        as_owner(stranger)
        _assert_refused(await _post(client, "hrms"))

    async def test_a_refusal_never_names_another_organizations_subscription(
        self, client: AsyncClient, org_id: uuid.UUID
    ) -> None:
        stranger = uuid.uuid4()
        sid = uuid.uuid4()
        await project_subscription_event(
            real_envelope(stranger, state="cancelled", subscription_id=sid)
        )
        as_owner(stranger)
        response = await _post(client)
        assert response.status_code == 409
        assert str(sid) not in response.text
        assert str(stranger) not in response.text


class TestAuthorization:
    """The gate is behind the same permission as ever, and the check comes first: a caller who may
    not activate learns nothing about what manages the product."""

    async def _managed(self, org_id: uuid.UUID) -> None:
        await project_subscription_event(real_envelope(org_id, state="cancelled"))

    async def test_without_the_permission_it_is_403_not_409(
        self, client: AsyncClient, org_id: uuid.UUID
    ) -> None:
        await self._managed(org_id)
        as_owner(org_id, perms=[])
        response = await _post(client)
        assert response.status_code == 403
        assert "subscription" not in response.text.lower()

    async def test_with_the_read_permission_alone_it_is_403(
        self, client: AsyncClient, org_id: uuid.UUID
    ) -> None:
        await self._managed(org_id)
        as_owner(org_id, perms=["core.product.activation.read:ORG"])
        assert (await _post(client)).status_code == 403

    async def test_a_member_with_only_own_scope_is_403(
        self, client: AsyncClient, org_id: uuid.UUID
    ) -> None:
        """Activation is an organization-wide act (`minimum=Scope.ORG`): the write permission at
        OWN scope is not enough, managed product or not."""
        await self._managed(org_id)
        as_owner(org_id, perms=[f"{WRITE}:OWN"])
        assert (await _post(client)).status_code == 403
        as_owner(uuid.uuid4(), perms=[f"{WRITE}:OWN"])
        assert (await _post(client)).status_code == 403, "nor on an organization with none"

    async def test_unauthenticated_is_401(self, client: AsyncClient, org_id: uuid.UUID) -> None:
        await self._managed(org_id)
        app.dependency_overrides.pop(get_principal, None)
        assert (await _post(client)).status_code == 401


class TestDeactivationAuthorization:
    """`DELETE /products/activations/{key}` gained a state-dependent 409. The permission check comes
    first, as it does on the POST: a caller who may not deactivate must not learn, from a 409
    instead of a 403, that an organization's product is fed by a subscription."""

    async def _live(self, org_id: uuid.UUID) -> None:
        """The refusing state: the product's own subscription is serving it."""
        await project_subscription_event(real_envelope(org_id, state="active"))

    async def test_without_the_permission_it_is_403_not_409(
        self, client: AsyncClient, org_id: uuid.UUID
    ) -> None:
        await self._live(org_id)
        as_owner(org_id, perms=[])
        response = await client.delete("/api/v1/products/activations/hrms")
        assert response.status_code == 403
        assert "subscription" not in response.text.lower()

    async def test_with_the_read_permission_alone_it_is_403(
        self, client: AsyncClient, org_id: uuid.UUID
    ) -> None:
        await self._live(org_id)
        as_owner(org_id, perms=["core.product.activation.read:ORG"])
        response = await client.delete("/api/v1/products/activations/hrms")
        assert response.status_code == 403
        assert "subscription" not in response.text.lower()

    async def test_a_member_with_only_own_scope_is_403(
        self, client: AsyncClient, org_id: uuid.UUID
    ) -> None:
        await self._live(org_id)
        as_owner(org_id, perms=[f"{WRITE}:OWN"])
        response = await client.delete("/api/v1/products/activations/hrms")
        assert response.status_code == 403
        assert "subscription" not in response.text.lower()

    async def test_unauthenticated_is_401(self, client: AsyncClient, org_id: uuid.UUID) -> None:
        await self._live(org_id)
        app.dependency_overrides.pop(get_principal, None)
        response = await client.delete("/api/v1/products/activations/hrms")
        assert response.status_code == 401
        assert "subscription" not in response.text.lower()

    async def test_none_of_the_refusals_wrote_anything(
        self, client: AsyncClient, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        await self._live(org_id)
        before = await _written(session, org_id)
        for perms in ([], ["core.product.activation.read:ORG"], [f"{WRITE}:OWN"]):
            as_owner(org_id, perms=perms)
            assert (await client.delete("/api/v1/products/activations/hrms")).status_code == 403
        assert await _written(session, org_id) == before

    async def test_one_organizations_live_subscription_does_not_refuse_anothers_deactivate(
        self, client: AsyncClient, org_id: uuid.UUID
    ) -> None:
        """Org A's live provenance is invisible to org B: B's hand activation deactivates as ever,
        while A, on the very same route, is refused on its own provenance."""
        stranger = uuid.uuid4()
        await self._live(stranger)

        as_owner(org_id)
        assert (await _post(client)).status_code == 201
        response = await client.delete("/api/v1/products/activations/hrms")
        assert response.status_code == 200, response.text
        assert response.json()["entitles"] is False

        as_owner(stranger)
        _assert_refused(await client.delete("/api/v1/products/activations/hrms"))


class TestDeactivation:
    """Optional slice: a live subscription-fed own source is not switched off by hand. Nothing in
    the dashboard, the e2e journeys or the demo tooling calls the route."""

    async def test_a_live_subscription_cannot_be_deactivated_by_hand(
        self, client: AsyncClient, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        await project_subscription_event(real_envelope(org_id, state="active"))
        before = await _written(session, org_id)

        as_owner(org_id)
        response = await client.delete("/api/v1/products/activations/hrms")
        _assert_refused(response)
        assert await _written(session, org_id) == before

    async def test_a_cancelled_subscription_deactivates_as_a_no_op(
        self, client: AsyncClient, org_id: uuid.UUID
    ) -> None:
        """Nothing live to refuse for: the request is the harmless one it always was."""
        await project_subscription_event(real_envelope(org_id, state="cancelled"))
        as_owner(org_id)
        response = await client.delete("/api/v1/products/activations/hrms")
        assert response.status_code == 200, response.text
        assert response.json()["entitles"] is False

    async def test_a_bundle_still_refuses_with_its_own_words(
        self, client: AsyncClient, org_id: uuid.UUID
    ) -> None:
        await project_subscription_event(
            real_envelope(org_id, state="active", product_key="suite", members=TRIO)
        )
        as_owner(org_id)
        response = await client.delete("/api/v1/products/activations/hrms")
        assert response.status_code == 422
        assert "included in your suite" in response.text

    async def test_a_hand_activation_deactivates_as_ever(
        self, client: AsyncClient, org_id: uuid.UUID
    ) -> None:
        as_owner(org_id)
        assert (await _post(client)).status_code == 201
        response = await client.delete("/api/v1/products/activations/hrms")
        assert response.status_code == 200
        assert response.json()["entitles"] is False


def test_the_error_is_the_platforms_coded_conflict() -> None:
    error = SubscriptionManagedConflict()
    assert error.status_code == 409
    assert error.problem_type == "conflict"
    assert error.code == CODE_SUBSCRIPTION_MANAGED == "subscription_managed"
    assert error.detail == SUBSCRIPTION_MANAGED_DETAIL == MESSAGE
    assert error.errors == [{"code": "subscription_managed", "message": MESSAGE}]
