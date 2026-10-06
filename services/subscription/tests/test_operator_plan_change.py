"""Staff plan switching on subscription's side: the hardening every operator write now gets, the
read-only preview, and the expected-state guard (docs/superpowers/specs/2026-10-06-admin-plan-
switch-design.md, ADR-0029).

Real PostgreSQL, the real internal routes with a real service token, a fake catalog and registry.
What is pinned:

* **Console only.** Suspend, subscribe, change-plan and its preview each refuse a token minted by
  any other service (401) and write nothing.
* **A person's words are a note.** They are the audit row's reason — never the transition's
  60-character machine `reason` (so a long reason is not a 500), and **never on the event**: the
  event is the customer org's, and its own notification templates can render any payload key. A
  word only the machinery writes (`renew`, `upgrade`...) is refused on every write, cancel
  included.
* **Refusals are machine-distinguishable**, and the state refusal wins over `billing_catching_up`.
* **A deferred change keeps its operator**, and applies with the event its direction names.
* **The preview answers what the write would do**, writes nothing, and cannot be pointed at another
  organization's subscription.
* **The expected-state guard** turns a stale confirm into a 409 `state_changed`.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app import domain, services
from app.api.internal import get_product_registry
from app.billing_run import BillingRunSweep
from app.config import settings
from app.main import app
from app.models import (
    SCHEMA,
    STATE_ACTIVE,
    STATE_CANCELLED,
    STATE_PAST_DUE,
    STATE_SUSPENDED,
    STATE_TRIAL,
    AuditLog,
    BillingPeriod,
    OutboxEvent,
    Subscription,
    SubscriptionTransition,
)
from app.schemas import CatalogPrice, OperatorChangePlanIn, OperatorSubscribeIn, SubscribeIn
from platform_core.auth import Scope, ScopeContext
from platform_core.db import get_session
from platform_core.internal_auth import mint_service_token
from tests.billing_fakes import FakeBilling
from tests.helpers import FakeCatalog, FakeRegistry, customer_principal, make_bundle, make_version

pytestmark = pytest.mark.integration

OPERATOR = "operator:11111111-1111-1111-1111-111111111111"


@pytest.fixture(autouse=True)
def _registry() -> Iterator[None]:
    app.dependency_overrides[get_product_registry] = lambda: FakeRegistry()
    yield
    app.dependency_overrides.pop(get_product_registry, None)


def _ctx(org_id: uuid.UUID) -> ScopeContext:
    return ScopeContext(
        principal=customer_principal(org_id),
        scope=Scope.ORG,
        permission="core.product.activation.write",
    )


def _headers(caller: str = "console") -> dict[str, str]:
    return {
        "X-Internal-Token": mint_service_token(settings(), audience="subscription", caller=caller)
    }


async def _subscribe(
    session: AsyncSession,
    catalog: FakeCatalog,
    org_id: uuid.UUID,
    amount_minor: int = 1000,
    *,
    product_key: str = "hrms",
    trial: bool = False,
    **version_kwargs: Any,
) -> tuple[uuid.UUID, Any]:
    version = catalog.add(
        make_version(product_key=product_key, amount_minor=amount_minor, **version_kwargs)
    )
    async with session.begin():
        sub = await services.subscribe(
            session,
            _ctx(org_id),
            catalog,
            SubscribeIn(
                product_key=product_key,
                plan_version_id=version.id,
                trial_days=14 if trial else None,
            ),
        )
        return sub.id, version


async def _read(session: AsyncSession, subscription_id: uuid.UUID) -> Subscription:
    session.expunge_all()
    async with session.begin():
        return await services.require_subscription(session, subscription_id)


async def _force(admin: AsyncSession, subscription_id: uuid.UUID, **columns: Any) -> None:
    """Move a subscription's columns directly (BYPASSRLS) — states the routes do not reach."""
    await admin.execute(
        update(Subscription).where(Subscription.id == subscription_id).values(**columns)
    )
    await admin.commit()


async def _audits(admin: AsyncSession, org_id: uuid.UUID, like: str = "%") -> list[AuditLog]:
    admin.expunge_all()
    rows = await admin.execute(
        select(AuditLog)
        .where(AuditLog.organization_id == org_id, AuditLog.action.like(like))
        .order_by(AuditLog.at)
    )
    return list(rows.scalars().all())


async def _events(admin: AsyncSession, org_id: uuid.UUID) -> list[OutboxEvent]:
    admin.expunge_all()
    rows = await admin.execute(
        select(OutboxEvent)
        .where(OutboxEvent.organization_id == org_id)
        .order_by(OutboxEvent.created_at)
    )
    return list(rows.scalars().all())


async def _counts(admin: AsyncSession) -> tuple[int, int, int]:
    """Audit, outbox and transition rows across the whole schema — a refusal writes none."""
    admin.expunge_all()
    audit = (await admin.execute(select(func.count()).select_from(AuditLog))).scalar_one()
    outbox = (await admin.execute(select(func.count()).select_from(OutboxEvent))).scalar_one()
    transitions = (
        await admin.execute(select(func.count()).select_from(SubscriptionTransition))
    ).scalar_one()
    return audit, outbox, transitions


def _change_body(org_id: uuid.UUID, version_id: uuid.UUID, **extra: Any) -> dict[str, Any]:
    return {
        "organization_id": str(org_id),
        "plan_version_id": str(version_id),
        "operator_id": OPERATOR,
        **extra,
    }


def _code(response: Any) -> str | None:
    errors = response.json().get("errors") or []
    return errors[0].get("code") if errors else None


async def _change(
    client: AsyncClient, sub_id: uuid.UUID, org_id: uuid.UUID, version_id: uuid.UUID, **extra: Any
) -> Any:
    return await client.post(
        f"/internal/v1/subscriptions/{sub_id}/change-plan",
        json=_change_body(org_id, version_id, **extra),
        headers=_headers(),
    )


async def _preview(
    client: AsyncClient, sub_id: uuid.UUID, org_id: uuid.UUID, version_id: uuid.UUID
) -> Any:
    return await client.post(
        f"/internal/v1/subscriptions/{sub_id}/change-plan/preview",
        json={"organization_id": str(org_id), "plan_version_id": str(version_id)},
        headers=_headers(),
    )


# ======================================================================== slice 0: hardening


class TestConsoleOnly:
    """Every operator write refuses a token that is valid, addressed here, and not console's."""

    @pytest.mark.parametrize("caller", [None, "tests", "billing", "catalog"])
    async def test_suspend_refuses_every_caller_but_console(
        self,
        client: AsyncClient,
        session: AsyncSession,
        admin_session: AsyncSession,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
        caller: str | None,
    ) -> None:
        sub_id, _ = await _subscribe(session, catalog, org_id)
        before = await _counts(admin_session)
        headers = {} if caller is None else _headers(caller)
        response = await client.post(
            f"/internal/v1/subscriptions/{sub_id}/suspend",
            json={"organization_id": str(org_id), "reason": "nonpayment"},
            headers=headers,
        )
        assert response.status_code == 401, response.text
        assert (await _read(session, sub_id)).state == STATE_ACTIVE
        assert await _counts(admin_session) == before

    async def test_console_still_suspends(
        self,
        client: AsyncClient,
        session: AsyncSession,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
    ) -> None:
        sub_id, _ = await _subscribe(session, catalog, org_id)
        response = await client.post(
            f"/internal/v1/subscriptions/{sub_id}/suspend",
            json={"organization_id": str(org_id), "reason": "nonpayment", "operator_id": "op-1"},
            headers=_headers(),
        )
        assert response.status_code == 200, response.text
        assert response.json()["state"] == STATE_SUSPENDED

    @pytest.mark.parametrize("caller", [None, "tests", "billing"])
    async def test_preview_and_change_plan_refuse_every_caller_but_console(
        self,
        client: AsyncClient,
        session: AsyncSession,
        admin_session: AsyncSession,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
        caller: str | None,
    ) -> None:
        sub_id, _ = await _subscribe(session, catalog, org_id, 1000)
        target = catalog.add(make_version(product_key="hrms", amount_minor=2000))
        before = await _counts(admin_session)
        headers = {} if caller is None else _headers(caller)
        for path, body in (
            (
                f"/internal/v1/subscriptions/{sub_id}/change-plan",
                _change_body(org_id, target.id, timing="next_period"),
            ),
            (
                f"/internal/v1/subscriptions/{sub_id}/change-plan/preview",
                {"organization_id": str(org_id), "plan_version_id": str(target.id)},
            ),
        ):
            response = await client.post(path, json=body, headers=headers)
            assert response.status_code == 401, (path, response.text)
        sub = await _read(session, sub_id)
        assert sub.pending_plan_version_id is None and sub.state == STATE_ACTIVE
        assert await _counts(admin_session) == before


class TestTheNoteIsNotTheTransitionReason:
    async def test_a_long_suspend_reason_is_a_note_not_a_500(
        self,
        client: AsyncClient,
        session: AsyncSession,
        admin_session: AsyncSession,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
    ) -> None:
        """Suspend accepts 200 characters; the transition's `reason` column holds 60. Before, a
        61-character reason was a database error and a 500 — the words now live in the audit
        row, and the transition keeps a machine code. They are not on the event: the customer
        org's own notification rules render its payload."""
        sub_id, _ = await _subscribe(session, catalog, org_id)
        words = "customer asked us to pause the account while their audit completes - " * 3
        words = words.strip()[:200]
        assert len(words) > 60

        response = await client.post(
            f"/internal/v1/subscriptions/{sub_id}/suspend",
            json={"organization_id": str(org_id), "reason": words, "operator_id": "op-1"},
            headers=_headers(),
        )
        assert response.status_code == 200, response.text

        admin_session.expunge_all()
        transition = (
            (
                await admin_session.execute(
                    select(SubscriptionTransition)
                    .where(SubscriptionTransition.subscription_id == sub_id)
                    .order_by(SubscriptionTransition.occurred_at.desc())
                )
            )
            .scalars()
            .first()
        )
        assert transition is not None and transition.reason == "operator_suspend"
        (audit,) = await _audits(admin_session, org_id, "commerce.subscription.suspended")
        assert audit.reason == words and audit.acting_as == "op-1"
        event = (await _events(admin_session, org_id))[-1]
        assert event.event_type == "commerce.subscription.suspended"
        assert "note" not in event.payload
        assert words not in str(event.payload)

    async def test_a_change_plan_note_over_sixty_characters_does_not_500(
        self,
        client: AsyncClient,
        session: AsyncSession,
        admin_session: AsyncSession,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
    ) -> None:
        sub_id, _ = await _subscribe(session, catalog, org_id, 1000, trial=True)
        target = catalog.add(make_version(product_key="hrms", amount_minor=2000))
        note = (
            "Sales agreed the Growth tier in the renewal call; see the ticket for the thread. " * 4
        )
        note = note.strip()[:300]
        response = await _change(client, sub_id, org_id, target.id, timing="next_period", note=note)
        assert response.status_code == 200, response.text
        assert response.json()["plan_version_id"] == str(target.id)  # a trial switches now

        (audit,) = await _audits(admin_session, org_id, "commerce.subscription.upgraded")
        assert audit.reason == note
        assert audit.acting_as == OPERATOR
        event = (await _events(admin_session, org_id))[-1]
        assert event.event_type == "commerce.subscription.upgraded"
        assert "note" not in event.payload
        assert note not in str(event.payload)
        admin_session.expunge_all()
        transition = (
            (
                await admin_session.execute(
                    select(SubscriptionTransition)
                    .where(SubscriptionTransition.subscription_id == sub_id)
                    .order_by(SubscriptionTransition.occurred_at.desc())
                )
            )
            .scalars()
            .first()
        )
        assert transition is not None and transition.reason == domain.REASON_UPGRADE

    @pytest.mark.parametrize("note", ["", "   ", "x" * 501])
    async def test_a_note_must_be_one_to_five_hundred_characters(
        self,
        client: AsyncClient,
        session: AsyncSession,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
        note: str,
    ) -> None:
        sub_id, _ = await _subscribe(session, catalog, org_id)
        target = catalog.add(make_version(product_key="hrms", amount_minor=2000))
        response = await _change(client, sub_id, org_id, target.id, note=note)
        assert response.status_code == 422, response.text
        assert (await _read(session, sub_id)).pending_plan_version_id is None

    async def test_no_note_keeps_every_existing_caller_working(
        self,
        client: AsyncClient,
        session: AsyncSession,
        admin_session: AsyncSession,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
    ) -> None:
        """A bundle rung change and console's price change send no note: the audit reason stays the
        machine reason, exactly as before."""
        sub_id, _ = await _subscribe(session, catalog, org_id, 1000, trial=True)
        target = catalog.add(make_version(product_key="hrms", amount_minor=2000))
        response = await _change(client, sub_id, org_id, target.id)
        assert response.status_code == 200, response.text
        (audit,) = await _audits(admin_session, org_id, "commerce.subscription.upgraded")
        assert audit.reason == domain.REASON_UPGRADE
        assert "note" not in (await _events(admin_session, org_id))[-1].payload


class TestReservedReasons:
    """A reason only the platform's machinery writes is refused on every write that takes words."""

    @pytest.mark.parametrize("word", ["renew", "upgrade", "downgrade", "payment_failed", " Renew "])
    async def test_every_operator_write_refuses_it(
        self,
        client: AsyncClient,
        session: AsyncSession,
        admin_session: AsyncSession,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
        word: str,
    ) -> None:
        sub_id, _ = await _subscribe(session, catalog, org_id, 1000)
        target = catalog.add(make_version(product_key="hrms", amount_minor=2000))
        suite = catalog.add(make_bundle(["hrms", "crm"], tier="duo", amount_minor=5000))
        before = await _counts(admin_session)

        responses = [
            await _change(client, sub_id, org_id, target.id, note=word),
            await client.post(
                f"/internal/v1/subscriptions/{sub_id}/suspend",
                json={"organization_id": str(org_id), "reason": word},
                headers=_headers(),
            ),
            await client.post(
                f"/internal/v1/subscriptions/{sub_id}/reactivate",
                json={"organization_id": str(org_id), "reason": word},
                headers=_headers(),
            ),
            await client.post(
                "/internal/v1/subscriptions",
                json={
                    "organization_id": str(org_id),
                    "product_key": "suite",
                    "plan_version_id": str(suite.id),
                    "operator_id": OPERATOR,
                    "note": word,
                },
                headers=_headers(),
            ),
        ]
        for response in responses:
            assert response.status_code == 422, response.text
            assert _code(response) == domain.CODE_RESERVED_REASON
        assert await _counts(admin_session) == before
        assert (await _read(session, sub_id)).state == STATE_ACTIVE

    async def test_a_customer_cancel_cannot_type_its_event(
        self, session: AsyncSession, catalog: FakeCatalog, org_id: uuid.UUID
    ) -> None:
        """`renew` as a cancel reason used to be fed to `decide`, which emitted a `renewed` event
        for a cancellation — missed by the console's churn count. Now it is refused, and an
        ordinary reason cancels with the cancel event."""
        sub_id, _ = await _subscribe(session, catalog, org_id)
        with pytest.raises(domain.CodedRefusal) as refused:
            async with session.begin():
                fresh = await services.require_subscription(session, sub_id)
                await services.cancel(session, _ctx(org_id), fresh, reason="renew")
        assert refused.value.code == domain.CODE_RESERVED_REASON
        assert (await _read(session, sub_id)).state == STATE_ACTIVE

        async with session.begin():
            fresh = await services.require_subscription(session, sub_id)
            await services.cancel(session, _ctx(org_id), fresh, reason="switching to a competitor")
        assert (await _read(session, sub_id)).state == STATE_CANCELLED


class TestCodedRefusalsAndPrecedence:
    async def test_the_state_refusal_comes_before_billing_catching_up(
        self,
        client: AsyncClient,
        session: AsyncSession,
        admin_session: AsyncSession,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
    ) -> None:
        """A past_due subscription whose period is also overdue used to be told "billing is
        catching up — try again shortly", for a change that will never be allowed. The refusal that
        is a property of the subscription now wins over the one that is transient."""
        sub_id, _ = await _subscribe(session, catalog, org_id, 1000)
        target = catalog.add(make_version(product_key="hrms", amount_minor=2000))
        await _force(
            admin_session,
            sub_id,
            state=STATE_PAST_DUE,
            current_period_end=datetime.now(UTC) - timedelta(days=2),
        )
        response = await _change(client, sub_id, org_id, target.id, timing="next_period")
        assert response.status_code == 422, response.text
        assert _code(response) == domain.CODE_STATE

    async def test_billing_catching_up_is_a_409_with_its_own_code(
        self,
        client: AsyncClient,
        session: AsyncSession,
        admin_session: AsyncSession,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
    ) -> None:
        sub_id, _ = await _subscribe(session, catalog, org_id, 1000)
        target = catalog.add(make_version(product_key="hrms", amount_minor=2000))
        await _force(
            admin_session, sub_id, current_period_end=datetime.now(UTC) - timedelta(days=2)
        )
        response = await _change(client, sub_id, org_id, target.id, timing="next_period")
        assert response.status_code == 409, response.text
        assert response.json()["title"] == "Billing is catching up"
        assert _code(response) == domain.CODE_BILLING_CATCHING_UP

    @pytest.mark.parametrize(
        ("target_kwargs", "status", "code"),
        [
            ({"currency": "EUR"}, 422, domain.CODE_CURRENCY),
            ({"billing_interval": "year"}, 422, domain.CODE_CADENCE),
            ({"billing_interval": "one_time"}, 422, domain.CODE_CADENCE),
            ({"product_key": "crm"}, 422, domain.CODE_PRODUCT_MISMATCH),
            ({"state": "draft"}, 422, domain.CODE_NOT_PUBLISHED),
        ],
    )
    async def test_every_refusal_carries_a_stable_code(
        self,
        client: AsyncClient,
        session: AsyncSession,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
        target_kwargs: dict[str, Any],
        status: int,
        code: str,
    ) -> None:
        sub_id, _ = await _subscribe(session, catalog, org_id, 1000)
        target = catalog.add(
            make_version(amount_minor=2000, **{"product_key": "hrms", **target_kwargs})
        )
        response = await _change(client, sub_id, org_id, target.id, timing="next_period")
        assert response.status_code == status, response.text
        assert _code(response) == code

    async def test_already_pinned_and_unknown_and_another_orgs_bespoke(
        self,
        client: AsyncClient,
        session: AsyncSession,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
        other_org: uuid.UUID,
    ) -> None:
        sub_id, current = await _subscribe(session, catalog, org_id, 1000)
        bespoke = catalog.add(
            make_version(product_key="hrms", amount_minor=500, restricted_to_org_id=other_org)
        )

        pinned = await _change(client, sub_id, org_id, current.id)
        assert (pinned.status_code, _code(pinned)) == (422, domain.CODE_ALREADY_PINNED)

        missing = await _change(client, sub_id, org_id, uuid.uuid4())
        foreign = await _change(client, sub_id, org_id, bespoke.id)
        assert (missing.status_code, foreign.status_code) == (404, 404)
        # No `not_visible`: the two bodies carry the same code and the same detail, so another
        # organization's negotiated price cannot be told from a version that does not exist.
        assert _code(missing) == _code(foreign) == domain.CODE_NOT_FOUND
        assert missing.json()["detail"] == foreign.json()["detail"]

    @pytest.mark.parametrize("state", ["draft", "grandfathered"])
    async def test_another_orgs_unpublished_bespoke_is_the_same_404_too(
        self,
        client: AsyncClient,
        session: AsyncSession,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
        other_org: uuid.UUID,
        state: str,
    ) -> None:
        """The audience check comes before the state check: a draft negotiated for another
        organization must not answer `not_published` (which says the id exists) on the write or
        the preview."""
        sub_id, _ = await _subscribe(session, catalog, org_id, 1000)
        draft = catalog.add(
            make_version(
                product_key="hrms",
                amount_minor=500,
                restricted_to_org_id=other_org,
                state=state,
            )
        )
        missing = await _change(client, sub_id, org_id, uuid.uuid4())
        for response in (
            await _change(client, sub_id, org_id, draft.id),
            await _preview(client, sub_id, org_id, draft.id),
        ):
            assert response.status_code == 404, response.text
            assert _code(response) == domain.CODE_NOT_FOUND
            assert response.json()["detail"] == missing.json()["detail"]


class TestDeferredChangeKeepsItsOperator:
    async def test_the_scheduled_and_withdrawn_audit_rows_carry_acting_as_and_the_note(
        self,
        client: AsyncClient,
        session: AsyncSession,
        admin_session: AsyncSession,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
    ) -> None:
        sub_id, current = await _subscribe(session, catalog, org_id, 1000)
        target = catalog.add(make_version(product_key="hrms", amount_minor=2000))

        scheduled = await _change(
            client, sub_id, org_id, target.id, timing="next_period", note="agreed on the call"
        )
        assert scheduled.status_code == 200, scheduled.text
        assert scheduled.json()["pending_plan_version_id"] == str(target.id)
        (row,) = await _audits(admin_session, org_id, "commerce.subscription.change_scheduled")
        assert row.acting_as == OPERATOR and row.reason == "agreed on the call"
        assert row.after["reason_code"] == domain.REASON_UPGRADE

        withdrawn = await _change(client, sub_id, org_id, current.id, note="customer changed mind")
        assert withdrawn.status_code == 200, withdrawn.text
        assert withdrawn.json()["pending_plan_version_id"] is None
        (cleared,) = await _audits(
            admin_session, org_id, "commerce.subscription.downgrade_withdrawn"
        )
        assert cleared.acting_as == OPERATOR and cleared.reason == "customer changed mind"
        assert cleared.before == {"pending_plan_version_id": str(target.id)}

    async def test_a_second_scheduled_change_names_the_one_it_replaced(
        self,
        client: AsyncClient,
        session: AsyncSession,
        admin_session: AsyncSession,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
    ) -> None:
        sub_id, _ = await _subscribe(session, catalog, org_id, 1000)
        first = catalog.add(make_version(product_key="hrms", amount_minor=2000))
        second = catalog.add(make_version(product_key="hrms", amount_minor=3000))
        for version in (first, second):
            response = await _change(client, sub_id, org_id, version.id, timing="next_period")
            assert response.status_code == 200, response.text
        rows = await _audits(admin_session, org_id, "commerce.subscription.change_scheduled")
        assert [r.after["pending_plan_version_id"] for r in rows] == [str(first.id), str(second.id)]
        assert rows[1].before["pending_plan_version_id"] == str(first.id)

    async def test_a_change_that_applies_now_supersedes_the_waiting_one_on_the_record(
        self,
        client: AsyncClient,
        session: AsyncSession,
        admin_session: AsyncSession,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
    ) -> None:
        """An equal-price change on a paying subscription applies now — and used to clear a
        waiting change with no audit row at all."""
        sub_id, _ = await _subscribe(session, catalog, org_id, 2000)
        cheaper = catalog.add(make_version(product_key="hrms", amount_minor=1000))
        same_price = catalog.add(make_version(product_key="hrms", amount_minor=2000))
        scheduled = await _change(client, sub_id, org_id, cheaper.id, timing="next_period")
        assert scheduled.json()["pending_plan_version_id"] == str(cheaper.id)

        applied = await _change(
            client, sub_id, org_id, same_price.id, timing="next_period", note="swap to new sheet"
        )
        assert applied.status_code == 200, applied.text
        assert applied.json()["plan_version_id"] == str(same_price.id)
        assert applied.json()["pending_plan_version_id"] is None
        (row,) = await _audits(admin_session, org_id, "commerce.subscription.change_superseded")
        assert row.acting_as == OPERATOR
        assert row.before == {"pending_plan_version_id": str(cheaper.id)}
        assert row.after["superseded_by_plan_version_id"] == str(same_price.id)


class TestAWithdrawnCurrentPin:
    """Catalog's Plans page can withdraw a version (`published -> grandfathered`, ADR-0030); every
    subscription already on it keeps it. Staying on it is therefore not a new pin: withdrawing a
    scheduled change back to it still works, on the preview and the write. Moving *onto* a
    withdrawn version is still refused."""

    async def test_a_scheduled_change_can_still_be_withdrawn(
        self,
        client: AsyncClient,
        session: AsyncSession,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
    ) -> None:
        sub_id, current = await _subscribe(session, catalog, org_id, 1000)
        cheaper = catalog.add(make_version(product_key="hrms", amount_minor=500))
        scheduled = await _change(client, sub_id, org_id, cheaper.id, timing="next_period")
        assert scheduled.status_code == 200, scheduled.text
        assert scheduled.json()["pending_plan_version_id"] == str(cheaper.id)

        catalog.add(current.model_copy(update={"state": "grandfathered"}))

        preview = await _preview(client, sub_id, org_id, current.id)
        assert preview.status_code == 200, preview.text
        assert preview.json()["action"] == "withdraw_scheduled"
        withdrawn = await _change(client, sub_id, org_id, current.id, note="staying put")
        assert withdrawn.status_code == 200, withdrawn.text
        assert withdrawn.json()["plan_version_id"] == str(current.id)
        assert withdrawn.json()["pending_plan_version_id"] is None

    async def test_with_nothing_scheduled_it_is_already_pinned_not_unpublished(
        self,
        client: AsyncClient,
        session: AsyncSession,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
    ) -> None:
        sub_id, current = await _subscribe(session, catalog, org_id, 1000)
        catalog.add(current.model_copy(update={"state": "grandfathered"}))
        response = await _change(client, sub_id, org_id, current.id)
        assert (response.status_code, _code(response)) == (422, domain.CODE_ALREADY_PINNED)

    async def test_moving_onto_another_withdrawn_version_is_still_refused(
        self,
        client: AsyncClient,
        session: AsyncSession,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
    ) -> None:
        sub_id, _ = await _subscribe(session, catalog, org_id, 1000)
        withdrawn = catalog.add(
            make_version(product_key="hrms", amount_minor=2000, state="grandfathered")
        )
        write = await _change(client, sub_id, org_id, withdrawn.id)
        assert (write.status_code, _code(write)) == (422, domain.CODE_NOT_PUBLISHED)
        preview = await _preview(client, sub_id, org_id, withdrawn.id)
        assert preview.status_code == 200, preview.text
        assert preview.json()["refusal_code"] == domain.CODE_NOT_PUBLISHED


class TestTheAppliedEventNamesItsDirection:
    async def _schedule(
        self,
        session: AsyncSession,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
        *,
        from_minor: int,
        to_minor: int,
        note: str | None = None,
    ) -> tuple[uuid.UUID, Any]:
        sub_id, _ = await _subscribe(session, catalog, org_id, from_minor, currency="INR")
        target = catalog.add(
            make_version(product_key="hrms", currency="INR", amount_minor=to_minor)
        )
        async with session.begin():
            fresh = await services.require_subscription(session, sub_id)
            await services.change_plan_as_operator(
                session,
                catalog,
                FakeRegistry(),
                fresh,
                OperatorChangePlanIn(
                    organization_id=org_id,
                    plan_version_id=target.id,
                    operator_id=OPERATOR,
                    timing="next_period",
                    note=note,
                ),
            )
        return sub_id, target

    async def _roll(self, run: BillingRunSweep, session: AsyncSession, sub_id: uuid.UUID) -> None:
        await run.tick(now=datetime.now(UTC))  # the opening claim
        sub = await _read(session, sub_id)
        assert sub.current_period_end is not None
        await run.tick(now=sub.current_period_end)  # the roll, which applies the change

    async def test_a_deferred_increase_emits_upgraded_and_keeps_the_note_on_the_audit_trail(
        self,
        run: BillingRunSweep,
        billing: FakeBilling,
        session: AsyncSession,
        admin_session: AsyncSession,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
    ) -> None:
        sub_id, target = await self._schedule(
            session, catalog, org_id, from_minor=100000, to_minor=200000, note="negotiated uplift"
        )
        await self._roll(run, session, sub_id)

        assert (await _read(session, sub_id)).plan_version_id == target.id
        types = [e.event_type for e in await _events(admin_session, org_id)]
        assert types.count("commerce.subscription.upgraded") == 1
        assert "commerce.subscription.downgraded" not in types
        applied = next(
            e
            for e in await _events(admin_session, org_id)
            if e.event_type == "commerce.subscription.upgraded"
        )
        assert "note" not in applied.payload
        assert "negotiated uplift" not in str(applied.payload)
        assert applied.payload["plan_version_id"] == str(target.id)
        # The why stays with staff: the applied transition's audit row carries it.
        (applied_audit,) = await _audits(admin_session, org_id, "commerce.subscription.upgraded")
        assert applied_audit.reason == "negotiated uplift"

    async def test_a_deferred_decrease_still_emits_downgraded(
        self,
        run: BillingRunSweep,
        billing: FakeBilling,
        session: AsyncSession,
        admin_session: AsyncSession,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
    ) -> None:
        sub_id, _ = await self._schedule(
            session, catalog, org_id, from_minor=200000, to_minor=100000
        )
        await self._roll(run, session, sub_id)
        types = [e.event_type for e in await _events(admin_session, org_id)]
        assert types.count("commerce.subscription.downgraded") == 1
        assert "commerce.subscription.upgraded" not in types

    async def test_a_change_scheduled_before_the_direction_was_stored_keeps_the_old_label(
        self,
        run: BillingRunSweep,
        billing: FakeBilling,
        session: AsyncSession,
        admin_session: AsyncSession,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
    ) -> None:
        """Backward compatible: a pending change already waiting when this ships has no `reason`
        and is applied exactly as it always was."""
        sub_id, _ = await self._schedule(
            session, catalog, org_id, from_minor=100000, to_minor=200000
        )
        await admin_session.execute(
            text(
                "UPDATE subscription.subscriptions SET pending_change = pending_change - 'reason' "
                "WHERE id = :id"
            ),
            {"id": sub_id},
        )
        await admin_session.commit()
        await self._roll(run, session, sub_id)
        types = [e.event_type for e in await _events(admin_session, org_id)]
        assert types.count("commerce.subscription.downgraded") == 1
        assert "commerce.subscription.upgraded" not in types


# ===================================================================== slice 1: preview + guard


class TestPreview:
    async def test_a_trial_switches_now_and_free(
        self, client: AsyncClient, session: AsyncSession, catalog: FakeCatalog, org_id: uuid.UUID
    ) -> None:
        sub_id, current = await _subscribe(session, catalog, org_id, 1000, trial=True)
        target = catalog.add(make_version(product_key="hrms", amount_minor=2000))
        response = await _preview(client, sub_id, org_id, target.id)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["action"] == "switch" and body["direction"] == "upgrade"
        assert body["effective"] == "now" and body["effective_at"] is None
        assert body["refusal_code"] is None
        assert body["expected"] == {
            "state": STATE_TRIAL,
            "plan_version_id": str(current.id),
            "pending_plan_version_id": None,
        }

    @pytest.mark.parametrize(
        ("to_minor", "direction", "effective"),
        [
            (2000, "upgrade", "next_period"),
            (500, "downgrade", "next_period"),
            (1000, "equal", "now"),
        ],
    )
    async def test_a_paying_subscription_waits_for_its_next_period_unless_nothing_is_owed(
        self,
        client: AsyncClient,
        session: AsyncSession,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
        to_minor: int,
        direction: str,
        effective: str,
    ) -> None:
        sub_id, _ = await _subscribe(session, catalog, org_id, 1000)
        target = catalog.add(make_version(product_key="hrms", amount_minor=to_minor))
        body = (await _preview(client, sub_id, org_id, target.id)).json()
        assert body["direction"] == direction and body["effective"] == effective
        sub = await _read(session, sub_id)
        if effective == "next_period":
            assert body["effective_at"] is not None
            assert datetime.fromisoformat(body["effective_at"]) == sub.current_period_end

    async def test_the_preview_and_the_write_agree(
        self,
        client: AsyncClient,
        session: AsyncSession,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
    ) -> None:
        """The preview is the write's own evaluation: whatever it says lands is what the write
        then does."""
        sub_id, _ = await _subscribe(session, catalog, org_id, 1000)
        up = catalog.add(make_version(product_key="hrms", amount_minor=2000))
        preview = (await _preview(client, sub_id, org_id, up.id)).json()
        written = await _change(
            client, sub_id, org_id, up.id, timing="next_period", expected=preview["expected"]
        )
        assert written.status_code == 200, written.text
        assert preview["effective"] == "next_period"
        assert written.json()["pending_plan_version_id"] == str(up.id)
        assert written.json()["plan_version_id"] != str(up.id)

    async def test_it_says_what_it_would_replace(
        self, client: AsyncClient, session: AsyncSession, catalog: FakeCatalog, org_id: uuid.UUID
    ) -> None:
        sub_id, current = await _subscribe(session, catalog, org_id, 1000)
        first = catalog.add(make_version(product_key="hrms", amount_minor=2000))
        second = catalog.add(make_version(product_key="hrms", amount_minor=3000))
        assert (await _preview(client, sub_id, org_id, first.id)).json()[
            "would_clear_pending"
        ] is False
        await _change(client, sub_id, org_id, first.id, timing="next_period")
        body = (await _preview(client, sub_id, org_id, second.id)).json()
        assert body["would_clear_pending"] is True
        assert body["expected"]["pending_plan_version_id"] == str(first.id)
        # Pointing at the pin it is already on offers the withdrawal.
        withdraw = (await _preview(client, sub_id, org_id, current.id)).json()
        assert withdraw["action"] == "withdraw_scheduled" and withdraw["direction"] is None
        assert withdraw["effective"] == "now" and withdraw["would_clear_pending"] is True

    async def test_a_bundle_move_names_the_products_it_adds_and_removes(
        self,
        client: AsyncClient,
        session: AsyncSession,
        admin_session: AsyncSession,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
    ) -> None:
        duo = catalog.add(make_bundle(["hrms", "crm"], tier="duo", amount_minor=5000))
        trio = catalog.add(make_bundle(["hrms", "crm", "finance"], tier="trio", amount_minor=8000))
        solo_pair = catalog.add(make_bundle(["hrms", "finance"], tier="duo", amount_minor=5000))
        async with session.begin():
            sub = await services.subscribe_as_operator(
                session,
                catalog,
                FakeRegistry(),
                OperatorSubscribeIn(
                    organization_id=org_id,
                    product_key="suite",
                    plan_version_id=duo.id,
                    operator_id=OPERATOR,
                ),
            )
            sub_id = sub.id
        up = (await _preview(client, sub_id, org_id, trio.id)).json()
        assert up["entitled_added"] == ["finance"] and up["entitled_removed"] == []
        swap = (await _preview(client, sub_id, org_id, solo_pair.id)).json()
        assert swap["entitled_added"] == ["finance"] and swap["entitled_removed"] == ["crm"]

    async def test_a_usage_price_is_flagged_because_direction_ignores_it(
        self, client: AsyncClient, session: AsyncSession, catalog: FakeCatalog, org_id: uuid.UUID
    ) -> None:
        sub_id, _ = await _subscribe(session, catalog, org_id, 1000)
        metered = make_version(product_key="hrms", amount_minor=1000)
        metered.prices.append(
            CatalogPrice(
                id=uuid.uuid4(),
                kind="usage",
                currency="USD",
                unit_price_micros=2_500_000,
                meter_key="api_calls",
            )
        )
        catalog.add(metered)
        body = (await _preview(client, sub_id, org_id, metered.id)).json()
        # Same recurring price, a usage rate besides: the direction is by the recurring part only
        # (the exact `_list_price_minor` rule the write uses) — and the preview says so.
        assert body["direction"] == "equal" and body["usage_priced"] is True
        # Only the *new* plan meters: nothing metered so far has a price to close at.
        assert body["usage_closed_now"] is False

    @pytest.mark.parametrize(
        ("trial", "to_minor", "effective", "closed"),
        [
            (False, 1000, "now", True),
            (False, 2000, "next_period", False),
            (True, 2000, "now", False),
        ],
        ids=["paying-equal-price-now", "paying-scheduled", "trial-now"],
    )
    async def test_it_says_when_applying_now_invoices_the_usage_so_far(
        self,
        client: AsyncClient,
        session: AsyncSession,
        admin_session: AsyncSession,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
        trial: bool,
        to_minor: int,
        effective: str,
        closed: bool,
    ) -> None:
        """An equal-price switch on a paying, metered subscription is not "nothing charged": the
        apply-now branch closes the usage so far at the current pin's usage rates, and the billing
        run invoices it. The preview says exactly when that happens, by the write's own rule — and
        the write then does what the preview said."""
        sub_id, current = await _subscribe(session, catalog, org_id, 1000, trial=trial)
        current.prices.append(
            CatalogPrice(
                id=uuid.uuid4(),
                kind="usage",
                currency="USD",
                unit_price_micros=2_500_000,
                meter_key="api_calls",
            )
        )
        target = catalog.add(make_version(product_key="hrms", amount_minor=to_minor))
        body = (await _preview(client, sub_id, org_id, target.id)).json()
        assert body["effective"] == effective and body["usage_priced"] is True
        assert body["usage_closed_now"] is closed

        written = await _change(
            client, sub_id, org_id, target.id, timing="next_period", expected=body["expected"]
        )
        assert written.status_code == 200, written.text
        admin_session.expunge_all()
        closes = (
            (
                await admin_session.execute(
                    select(BillingPeriod).where(
                        BillingPeriod.subscription_id == sub_id,
                        BillingPeriod.kind == domain.KIND_CLOSE,
                    )
                )
            )
            .scalars()
            .all()
        )
        assert bool(closes) is closed
        if closed:
            (close,) = closes
            assert close.plan_version_id == current.id  # priced at the pin it was metered on

    async def test_a_per_seat_price_counts_at_quantity_one(
        self, client: AsyncClient, session: AsyncSession, catalog: FakeCatalog, org_id: uuid.UUID
    ) -> None:
        sub_id, _ = await _subscribe(session, catalog, org_id, 1000)
        seated = make_version(product_key="hrms", amount_minor=600)
        seated.prices.append(
            CatalogPrice(
                id=uuid.uuid4(), kind="per_seat", amount_minor=500, currency="USD", unit="seat"
            )
        )
        catalog.add(seated)
        body = (await _preview(client, sub_id, org_id, seated.id)).json()
        assert body["direction"] == "upgrade"  # 600 + 500 > 1000, integers only

    @pytest.mark.parametrize(
        ("force", "target_kwargs", "code"),
        [
            ({"state": STATE_PAST_DUE}, {}, domain.CODE_STATE),
            ({"state": STATE_SUSPENDED}, {}, domain.CODE_STATE),
            ({}, {"currency": "EUR"}, domain.CODE_CURRENCY),
            ({}, {"billing_interval": "year"}, domain.CODE_CADENCE),
            ({}, {"product_key": "crm"}, domain.CODE_PRODUCT_MISMATCH),
            ({}, {"state": "draft"}, domain.CODE_NOT_PUBLISHED),
        ],
    )
    async def test_a_refusal_is_an_answer_not_an_error(
        self,
        client: AsyncClient,
        session: AsyncSession,
        admin_session: AsyncSession,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
        force: dict[str, Any],
        target_kwargs: dict[str, Any],
        code: str,
    ) -> None:
        sub_id, _ = await _subscribe(session, catalog, org_id, 1000)
        if force:
            await _force(admin_session, sub_id, **force)
        session.expunge_all()  # a request has a fresh session; this test shares one
        target = catalog.add(
            make_version(amount_minor=2000, **{"product_key": "hrms", **target_kwargs})
        )
        response = await _preview(client, sub_id, org_id, target.id)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["refusal_code"] == code and body["refusal_detail"]
        assert body["direction"] is None and body["effective"] is None

    async def test_already_pinned_and_billing_catching_up_are_answers_too(
        self,
        client: AsyncClient,
        session: AsyncSession,
        admin_session: AsyncSession,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
    ) -> None:
        sub_id, current = await _subscribe(session, catalog, org_id, 1000)
        target = catalog.add(make_version(product_key="hrms", amount_minor=2000))
        assert (await _preview(client, sub_id, org_id, current.id)).json()["refusal_code"] == (
            domain.CODE_ALREADY_PINNED
        )
        await _force(
            admin_session, sub_id, current_period_end=datetime.now(UTC) - timedelta(days=1)
        )
        session.expunge_all()  # a request has a fresh session; this test shares one
        assert (await _preview(client, sub_id, org_id, target.id)).json()["refusal_code"] == (
            domain.CODE_BILLING_CATCHING_UP
        )

    async def test_an_unknown_version_and_another_orgs_bespoke_are_the_same_404(
        self,
        client: AsyncClient,
        session: AsyncSession,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
        other_org: uuid.UUID,
    ) -> None:
        sub_id, _ = await _subscribe(session, catalog, org_id, 1000)
        bespoke = catalog.add(
            make_version(product_key="hrms", amount_minor=500, restricted_to_org_id=other_org)
        )
        missing = await _preview(client, sub_id, org_id, uuid.uuid4())
        foreign = await _preview(client, sub_id, org_id, bespoke.id)
        assert (missing.status_code, foreign.status_code) == (404, 404)
        assert missing.json()["detail"] == foreign.json()["detail"]
        assert _code(missing) == _code(foreign) == domain.CODE_NOT_FOUND

    async def test_it_writes_nothing(
        self,
        client: AsyncClient,
        session: AsyncSession,
        admin_session: AsyncSession,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
    ) -> None:
        sub_id, _ = await _subscribe(session, catalog, org_id, 1000)
        up = catalog.add(make_version(product_key="hrms", amount_minor=2000))
        before = await _counts(admin_session)
        for _ in range(3):
            assert (await _preview(client, sub_id, org_id, up.id)).status_code == 200
        assert await _counts(admin_session) == before
        sub = await _read(session, sub_id)
        assert sub.pending_plan_version_id is None and sub.pending_change is None


class TestCrossTenant:
    """The subscription side of the isolation console's mocked tests cannot prove: the route binds
    the tenant the *caller names*, and RLS then confirms the subscription really is theirs."""

    async def test_another_organizations_subscription_is_a_404_on_every_route(
        self,
        client: AsyncClient,
        session: AsyncSession,
        admin_session: AsyncSession,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
        other_org: uuid.UUID,
    ) -> None:
        sub_id, _ = await _subscribe(session, catalog, org_id, 1000)
        target = catalog.add(make_version(product_key="hrms", amount_minor=2000))
        before = await _counts(admin_session)

        # Org B's name on org A's subscription id.
        for response in (
            await _change(client, sub_id, other_org, target.id, timing="next_period"),
            await _preview(client, sub_id, other_org, target.id),
            await client.post(
                f"/internal/v1/subscriptions/{sub_id}/suspend",
                json={"organization_id": str(other_org), "reason": "nonpayment"},
                headers=_headers(),
            ),
        ):
            assert response.status_code == 404, response.text
        sub = await _read(session, sub_id)
        assert sub.state == STATE_ACTIVE and sub.pending_plan_version_id is None
        assert await _counts(admin_session) == before

    async def test_the_named_organization_is_filtered_on_not_left_to_rls_alone(
        self,
        client: AsyncClient,
        session: AsyncSession,
        admin_session: AsyncSession,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
        other_org: uuid.UUID,
    ) -> None:
        """CLAUDE.md: every query filters on the organization; RLS is the net, not permission to
        skip the filter. With the net taken away — a BYPASSRLS session standing in for a role
        recreated with it, or a dropped policy — org B's name on org A's subscription id is still
        a 404 on every operator route, and nothing of org A's is read or written."""
        sub_id, _ = await _subscribe(session, catalog, org_id, 1000)
        target = catalog.add(make_version(product_key="hrms", amount_minor=2000))
        before = await _counts(admin_session)

        engine = create_async_engine(
            settings().admin_dsn,
            connect_args={"server_settings": {"search_path": f"{SCHEMA},public"}},
        )
        maker = async_sessionmaker(engine, expire_on_commit=False)

        async def _no_rls_net() -> AsyncIterator[AsyncSession]:
            async with maker() as unpoliced:
                yield unpoliced

        previous = app.dependency_overrides[get_session]
        app.dependency_overrides[get_session] = _no_rls_net
        try:
            responses = [
                await _change(client, sub_id, other_org, target.id, timing="next_period"),
                await _preview(client, sub_id, other_org, target.id),
                await client.post(
                    f"/internal/v1/subscriptions/{sub_id}/suspend",
                    json={"organization_id": str(other_org), "reason": "nonpayment"},
                    headers=_headers(),
                ),
                await client.post(
                    f"/internal/v1/subscriptions/{sub_id}/reactivate",
                    json={"organization_id": str(other_org), "reason": "paid up"},
                    headers=_headers(),
                ),
            ]
        finally:
            app.dependency_overrides[get_session] = previous
            await engine.dispose()
        for response in responses:
            assert response.status_code == 404, response.text
        sub = await _read(session, sub_id)
        assert sub.state == STATE_ACTIVE and sub.pending_plan_version_id is None
        assert await _counts(admin_session) == before


class TestExpectedStateGuard:
    async def test_a_matching_expected_state_applies(
        self, client: AsyncClient, session: AsyncSession, catalog: FakeCatalog, org_id: uuid.UUID
    ) -> None:
        sub_id, current = await _subscribe(session, catalog, org_id, 1000)
        target = catalog.add(make_version(product_key="hrms", amount_minor=2000))
        response = await _change(
            client,
            sub_id,
            org_id,
            target.id,
            timing="next_period",
            expected={
                "state": STATE_ACTIVE,
                "plan_version_id": str(current.id),
                "pending_plan_version_id": None,
            },
        )
        assert response.status_code == 200, response.text

    @pytest.mark.parametrize("drift", ["state", "pin", "pending"])
    async def test_anything_that_moved_in_between_is_a_409_that_writes_nothing(
        self,
        client: AsyncClient,
        session: AsyncSession,
        admin_session: AsyncSession,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
        drift: str,
    ) -> None:
        sub_id, current = await _subscribe(session, catalog, org_id, 1000)
        target = catalog.add(make_version(product_key="hrms", amount_minor=2000))
        other = catalog.add(make_version(product_key="hrms", amount_minor=3000))
        expected = {
            "state": STATE_ACTIVE,
            "plan_version_id": str(current.id),
            "pending_plan_version_id": None,
        }
        # The subscription moves after the preview and before the confirm.
        if drift == "state":
            await _force(admin_session, sub_id, state=STATE_PAST_DUE)
        elif drift == "pin":
            await _force(admin_session, sub_id, plan_version_id=other.id)
        else:
            await _change(client, sub_id, org_id, other.id, timing="next_period")
        before = await _counts(admin_session)

        response = await _change(
            client, sub_id, org_id, target.id, timing="next_period", expected=expected
        )
        assert response.status_code == 409, response.text
        assert _code(response) == domain.CODE_STATE_CHANGED
        assert await _counts(admin_session) == before
        assert (await _read(session, sub_id)).pending_plan_version_id != target.id

    async def test_state_changed_is_decided_before_the_state_refusal(
        self,
        client: AsyncClient,
        session: AsyncSession,
        admin_session: AsyncSession,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
    ) -> None:
        """A subscription that went past_due after the preview is told *it changed*, not merely
        that a past_due subscription cannot switch: the operator's picture is what is stale."""
        sub_id, current = await _subscribe(session, catalog, org_id, 1000)
        target = catalog.add(make_version(product_key="hrms", amount_minor=2000))
        await _force(admin_session, sub_id, state=STATE_PAST_DUE)
        response = await _change(
            client,
            sub_id,
            org_id,
            target.id,
            expected={
                "state": STATE_ACTIVE,
                "plan_version_id": str(current.id),
                "pending_plan_version_id": None,
            },
        )
        assert _code(response) == domain.CODE_STATE_CHANGED


class TestOperatorSubscribeNote:
    async def test_the_note_is_the_audit_reason_and_never_rides_the_event(
        self,
        client: AsyncClient,
        session: AsyncSession,
        admin_session: AsyncSession,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
    ) -> None:
        duo = catalog.add(make_bundle(["hrms", "crm"], tier="duo", amount_minor=5000))
        response = await client.post(
            "/internal/v1/subscriptions",
            json={
                "organization_id": str(org_id),
                "product_key": "suite",
                "plan_version_id": str(duo.id),
                "operator_id": OPERATOR,
                "note": "sold on the enterprise call",
            },
            headers=_headers(),
        )
        assert response.status_code == 201, response.text
        (audit,) = await _audits(admin_session, org_id, "commerce.subscription.activated")
        assert audit.reason == "sold on the enterprise call" and audit.acting_as == OPERATOR
        event = (await _events(admin_session, org_id))[-1]
        assert "note" not in event.payload
        assert "sold on the enterprise call" not in str(event.payload)
