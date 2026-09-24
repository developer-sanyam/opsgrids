"""`app/backfill.py` — the one-off that fills the projection gap for every organization and
subscription that predates console's own event queue.

Both HTTP boundaries (`account_client.list_organizations`, `subscription_client.list_subscriptions`)
are faked here rather than mocked at the transport layer: the backfill's own contract is what it
does with the rows those calls return, not how it calls them (that shape is already covered by
`directory_client`/`subscription_client`'s own module docstrings and console's other client-level
tests). `resolve_price` is the same fake resolver `test_metrics.py` uses in place of catalog.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import account_client, consumers, selectors, subscription_client
from app.backfill import BackfillReport, backfill
from app.config import settings
from app.models import OutboxEvent, RevenueEvent
from app.price import PriceResolver
from app.services import fold_org_event, fold_subscription_event
from platform_core.db import session_scope
from tests.helpers import envelope, subscription_payload

pytestmark = pytest.mark.integration

PV1 = "11111111-1111-1111-1111-111111111111"


async def _resolve_price(_plan_version_id: str) -> tuple[int, str | None]:
    return 50000, "INR"


RESOLVE_PRICE: PriceResolver = _resolve_price


def _org_row(
    org_id: uuid.UUID,
    *,
    name: str = "Northwind Metals",
    slug: str = "northwind-metals",
    updated_at: datetime | None = None,
) -> dict[str, Any]:
    return {
        "id": str(org_id),
        "name": name,
        "slug": slug,
        "status": "active",
        "updated_at": (updated_at or datetime.now(UTC)).isoformat(),
    }


def _sub_row(
    *,
    product_key: str = "hrms",
    state: str = "active",
    plan_version_id: str | None = PV1,
    trial_ends_at: str | None = None,
    currency: str = "INR",
    updated_at: datetime | None = None,
) -> dict[str, Any]:
    return {
        "id": str(uuid.uuid4()),
        "product_key": product_key,
        "state": state,
        "plan_version_id": plan_version_id,
        "trial_ends_at": trial_ends_at,
        "currency": currency,
        "updated_at": (updated_at or datetime.now(UTC)).isoformat(),
    }


def _fake_organizations(
    rows: list[dict[str, Any]],
) -> Callable[..., Awaitable[list[dict[str, Any]]]]:
    async def fake(_settings: Any, *, client: Any) -> list[dict[str, Any]]:
        return rows

    return fake


def _fake_subscriptions(
    rows_by_org: dict[uuid.UUID, list[dict[str, Any]]],
) -> Callable[..., Awaitable[list[dict[str, Any]]]]:
    async def fake(
        _settings: Any, organization_id: uuid.UUID, *, client: Any
    ) -> list[dict[str, Any]]:
        return rows_by_org.get(organization_id, [])

    return fake


def _work_scope(organization_id: uuid.UUID) -> AbstractAsyncContextManager[AsyncSession]:
    return session_scope(organization_id=organization_id)


async def _run(
    monkeypatch: pytest.MonkeyPatch,
    *,
    orgs: list[dict[str, Any]],
    subs_by_org: dict[uuid.UUID, list[dict[str, Any]]],
) -> BackfillReport:
    monkeypatch.setattr(account_client, "list_organizations", _fake_organizations(orgs))
    monkeypatch.setattr(subscription_client, "list_subscriptions", _fake_subscriptions(subs_by_org))
    # Never opens a real connection — both clients above are faked before the client is ever
    # handed to them — but `backfill()` takes a real `httpx.AsyncClient` rather than `| None`,
    # the same shape every other client module in this service does.
    async with httpx.AsyncClient() as client:
        return await backfill(
            settings=settings(),
            work_scope=_work_scope,
            resolve_price=RESOLVE_PRICE,
            client=client,
        )


class TestBackfillCreatesProjections:
    async def test_a_fresh_run_creates_the_projection(
        self,
        monkeypatch: pytest.MonkeyPatch,
        engine_ready: None,
        directory_session: AsyncSession,
        org_id: uuid.UUID,
    ) -> None:
        report = await _run(
            monkeypatch,
            orgs=[_org_row(org_id)],
            subs_by_org={org_id: [_sub_row()]},
        )

        assert report.orgs_seen == 1
        assert report.created == 1
        assert report.updated == 0
        assert report.unchanged == 0
        assert report.failed == 0

        states = await selectors.all_org_states(directory_session)
        assert len(states) == 1
        assert states[0].organization_id == org_id
        assert states[0].name == "Northwind Metals"
        assert states[0].slug == "northwind-metals"
        assert states[0].subscription_states["hrms"]["state"] == "active"
        assert states[0].mrr_minor == 50000

    async def test_an_organization_with_no_subscriptions_still_gets_its_identity(
        self,
        monkeypatch: pytest.MonkeyPatch,
        engine_ready: None,
        directory_session: AsyncSession,
        org_id: uuid.UUID,
    ) -> None:
        report = await _run(monkeypatch, orgs=[_org_row(org_id)], subs_by_org={})

        assert report.created == 1
        states = await selectors.all_org_states(directory_session)
        assert states[0].name == "Northwind Metals"
        assert states[0].subscription_states == {}


class TestBackfillIsIdempotent:
    async def test_a_second_run_against_unchanged_data_is_a_no_op(
        self,
        monkeypatch: pytest.MonkeyPatch,
        engine_ready: None,
        directory_session: AsyncSession,
        org_id: uuid.UUID,
    ) -> None:
        org_row = _org_row(org_id)
        sub_row = _sub_row()

        first = await _run(monkeypatch, orgs=[org_row], subs_by_org={org_id: [sub_row]})
        assert first.created == 1

        second = await _run(monkeypatch, orgs=[org_row], subs_by_org={org_id: [sub_row]})
        assert second.orgs_seen == 1
        assert second.created == 0
        assert second.updated == 0
        assert second.unchanged == 1, "re-running against the identical source data must be a no-op"

        states = await selectors.all_org_states(directory_session)
        assert len(states) == 1
        assert states[0].name == "Northwind Metals"
        assert states[0].mrr_minor == 50000

    async def test_a_run_that_finds_a_real_change_reports_updated_not_unchanged(
        self,
        monkeypatch: pytest.MonkeyPatch,
        engine_ready: None,
        directory_session: AsyncSession,
        org_id: uuid.UUID,
    ) -> None:
        t0 = datetime.now(UTC)
        first = await _run(
            monkeypatch,
            orgs=[_org_row(org_id, updated_at=t0)],
            subs_by_org={org_id: [_sub_row(updated_at=t0)]},
        )
        assert first.created == 1

        # account renamed the organization; subscription-service's row is unchanged.
        second = await _run(
            monkeypatch,
            orgs=[_org_row(org_id, name="Northwind Group", updated_at=t0 + timedelta(minutes=5))],
            subs_by_org={org_id: [_sub_row(updated_at=t0)]},
        )
        assert second.updated == 1
        assert second.created == 0

        directory_session.expire_all()
        states = await selectors.all_org_states(directory_session)
        assert states[0].name == "Northwind Group"


class TestBackfillNeverOverwritesNewerState:
    async def test_it_does_not_overwrite_a_name_a_live_event_already_set(
        self,
        monkeypatch: pytest.MonkeyPatch,
        engine_ready: None,
        directory_session: AsyncSession,
        org_id: uuid.UUID,
    ) -> None:
        """A live `core.org.updated` renamed the organization after the moment the backfill's own
        discovery snapshot is stamped with; the backfill must not resurrect the stale name."""
        t0 = datetime.now(UTC)
        live_rename_at = t0 + timedelta(minutes=10)

        await consumers.consume(
            envelope(
                "core.org.updated",
                organization_id=org_id,
                payload={
                    "organization_id": str(org_id),
                    "changed": ["name"],
                    "name": "Renamed By A Live Event",
                },
                occurred_at=live_rename_at,
            ),
            fold_org_event,
        )

        # The backfill discovers account's row with an *older* `updated_at` than the live rename
        # above — e.g. a discovery read that raced the rename, or a retried run against a stale
        # cache. It must not win.
        report = await _run(
            monkeypatch,
            orgs=[_org_row(org_id, name="Stale Name", updated_at=t0)],
            subs_by_org={},
        )
        assert report.updated == 0

        directory_session.expire_all()
        states = await selectors.all_org_states(directory_session)
        assert states[0].name == "Renamed By A Live Event"

    async def test_it_does_not_overwrite_a_subscription_state_a_live_event_already_set(
        self,
        monkeypatch: pytest.MonkeyPatch,
        engine_ready: None,
        directory_session: AsyncSession,
        org_id: uuid.UUID,
    ) -> None:
        t0 = datetime.now(UTC)
        live_cancel_at = t0 + timedelta(minutes=10)

        async def fold_sub(session: Any, env: dict[str, Any]) -> None:
            await fold_subscription_event(session, env, resolve_price=RESOLVE_PRICE)

        await consumers.consume(
            envelope(
                "commerce.subscription.cancelled",
                organization_id=org_id,
                payload=subscription_payload(
                    org_id, product_key="hrms", state="cancelled", plan_version_id=PV1
                ),
                occurred_at=live_cancel_at,
            ),
            fold_sub,
        )

        # The backfill's own discovery snapshot of subscription-service's row is stale relative to
        # the live cancellation above — it must not resurrect `active`. (The organization's
        # *identity* is still legitimately new to this projection — the live event above only
        # ever touched `subscription_states` — so this run does report an update; what matters is
        # which half of the snapshot changed.)
        await _run(
            monkeypatch,
            orgs=[_org_row(org_id)],
            subs_by_org={org_id: [_sub_row(state="active", updated_at=t0)]},
        )

        directory_session.expire_all()
        states = await selectors.all_org_states(directory_session)
        assert states[0].subscription_states["hrms"]["state"] == "cancelled"
        assert states[0].name == "Northwind Metals", "the identity half still backfills normally"


class TestBackfillEmitsNothing:
    async def test_it_writes_no_outbox_rows_and_no_revenue_events(
        self,
        monkeypatch: pytest.MonkeyPatch,
        engine_ready: None,
        admin_session: AsyncSession,
        directory_session: AsyncSession,
        org_id: uuid.UUID,
    ) -> None:
        """A backfilled snapshot is not an observed transition — console publishes nothing to begin
        with, and a synthetic revenue-log row per run would corrupt the append-only history with a
        row that never happened. notification only ever consumes commerce events, so an empty
        outbox here is also the proof it never gets a reason to send a trial email for this.

        `outbox` on the BYPASSRLS admin session: the directory reader has no grant on it at all
        (only the four projection/audit tables, `migrations/env.py`'s `DIRECTORY_TABLES`), and
        that grant boundary is not this test's concern.
        """
        await _run(monkeypatch, orgs=[_org_row(org_id)], subs_by_org={org_id: [_sub_row()]})

        outbox = (await admin_session.execute(select(OutboxEvent))).scalars().all()
        assert outbox == []

        revenue = (await directory_session.execute(select(RevenueEvent))).scalars().all()
        assert revenue == []
