"""D3: the provider call must not run while a database transaction is open.

`create_charge` used to be one transaction spanning the provider round-trip: claim the row, call
the provider, record what it said, commit. A slow provider held a pool connection (and, briefly,
the idempotency insert's row) for as long as it took to answer. These tests prove the restructured
shape — `stage_charge` / `call_provider_charge` / `finalize_charge`, three acts with the call in
the middle holding no session — and that a process which dies between the call and the record is
resumed by `app/cron.py`'s stuck-charge sweep rather than left stuck forever.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import services
from app.cron import StuckChargeSweep
from app.models import CHARGE_CREATED, Charge
from app.providers.razorpay import RazorpayProvider
from app.schemas import ChargeIn
from platform_core.db import get_outbox_sessionmaker, session_scope
from tests.helpers import service_token

pytestmark = pytest.mark.integration

CHARGES = "/internal/v1/charges"


def _auth() -> dict[str, str]:
    return {"X-Internal-Token": service_token()}


def _payload(org_id: uuid.UUID, *, key: str, **over: object) -> dict:
    body: dict = {
        "organization_id": str(org_id),
        "amount_minor": 1000,
        "currency": "USD",
        "idempotency_key": key,
    }
    body.update(over)
    return body


class TestNoTransactionDuringTheProviderCall:
    async def test_the_internal_route_holds_no_transaction_while_the_provider_is_called(
        self,
        client: AsyncClient,
        session: AsyncSession,
        org_id: uuid.UUID,
        provider: RazorpayProvider,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The route's claim transaction must have committed, and the record transaction must not
        yet have opened, at the moment the provider is asked."""
        seen_in_transaction: list[bool] = []
        original_charge = provider.charge

        def _spy(**kwargs: object) -> object:
            seen_in_transaction.append(session.in_transaction())
            return original_charge(**kwargs)

        monkeypatch.setattr(provider, "charge", _spy)

        response = await client.post(
            CHARGES, headers=_auth(), json=_payload(org_id, key="no-txn-route")
        )
        assert response.status_code == 201, response.text
        assert seen_in_transaction == [False], (
            "the provider was called while a transaction was open on the request's session"
        )

    async def test_the_consumer_holds_no_transaction_while_the_provider_is_called(
        self,
        engines: None,
        org_id: uuid.UUID,
        provider: RazorpayProvider,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Same property, driven through `app.consumers.charge_on_invoice_finalized` — the
        event-driven path billing also uses. Tracks how many of the consumer's own
        `session_scope` transactions are open at the moment the provider is called, rather than
        reaching for a session the consumer never shares with the test."""
        import app.consumers as consumers_module

        monkeypatch.setattr(consumers_module, "get_provider", lambda: provider)

        open_scopes = 0

        @asynccontextmanager
        async def _tracking_session_scope(organization_id: uuid.UUID | None = None) -> Any:
            nonlocal open_scopes
            open_scopes += 1
            try:
                async with session_scope(organization_id=organization_id) as s:
                    yield s
            finally:
                open_scopes -= 1

        monkeypatch.setattr(consumers_module, "session_scope", _tracking_session_scope)

        seen_open_scopes: list[int] = []
        original_charge = provider.charge

        def _spy(**kwargs: object) -> object:
            seen_open_scopes.append(open_scopes)
            return original_charge(**kwargs)

        monkeypatch.setattr(provider, "charge", _spy)

        invoice_id = uuid.uuid4()
        envelope = {
            "event_id": str(uuid.uuid4()),
            "event_type": "commerce.invoice.finalized",
            "organization_id": str(org_id),
            "payload": {
                "organization_id": str(org_id),
                "invoice_id": str(invoice_id),
                "total_minor": 1500,
                "currency": "USD",
            },
        }
        await consumers_module.charge_on_invoice_finalized(envelope)

        assert seen_open_scopes == [0], (
            "the provider was called while a consumer session_scope transaction was open"
        )

        async with session_scope(organization_id=org_id) as check:
            stored = (
                await check.execute(select(Charge).where(Charge.invoice_id == invoice_id))
            ).scalar_one()
        assert stored.provider_payment_id is not None
        assert stored.state == "authorized"


class TestCrashBetweenCallAndRecord:
    async def test_a_charge_stuck_after_the_call_is_resumed_by_the_sweep(
        self,
        engines: None,
        session: AsyncSession,
        org_id: uuid.UUID,
        provider: RazorpayProvider,
    ) -> None:
        """Simulate the crash directly: stage a charge and leave it exactly as a process dying
        right after `stage_charge` committed would — nothing else run yet. The sweep must find
        it, call the provider itself (proving *the sweep's* call is safe, not a replayed result),
        and record what it said."""
        payload = ChargeIn(
            organization_id=org_id,
            amount_minor=4200,
            currency="USD",
            idempotency_key="stuck-1",
        )
        async with session.begin():
            charge, needs_call = await services.stage_charge(session, provider, payload)
        assert needs_call is True
        assert charge.provider_payment_id is None
        assert charge.state == CHARGE_CREATED
        charge_id = charge.id

        sweep = StuckChargeSweep(
            discovery_factory=get_outbox_sessionmaker(),
            work_scope=lambda organization_id: session_scope(organization_id=organization_id),
            provider_factory=lambda: provider,
            reclaim_after=timedelta(seconds=0),
        )
        # Past the stager's lease: until it lapses, the charge's own call may still be in flight
        # and nobody else may make one (D3 round 3).
        report = await sweep.tick(now=datetime.now(UTC) + timedelta(minutes=10))
        assert report.considered == 1
        assert report.resumed == 1
        assert report.failed == 0

        async with session_scope(organization_id=org_id) as check:
            resumed = (
                await check.execute(select(Charge).where(Charge.id == charge_id))
            ).scalar_one()
        assert resumed.provider_payment_id is not None
        assert resumed.state == "authorized"

    async def test_a_healthy_in_flight_charge_is_left_alone(
        self,
        engines: None,
        session: AsyncSession,
        org_id: uuid.UUID,
        provider: RazorpayProvider,
    ) -> None:
        """The poisoned-step guard: a charge staged moments ago is not reclaimed just because it
        has no `provider_payment_id` yet — it might still be inside the very request that staged
        it."""
        payload = ChargeIn(
            organization_id=org_id,
            amount_minor=100,
            currency="USD",
            idempotency_key="fresh-1",
        )
        async with session.begin():
            await services.stage_charge(session, provider, payload)

        sweep = StuckChargeSweep(
            discovery_factory=get_outbox_sessionmaker(),
            work_scope=lambda organization_id: session_scope(organization_id=organization_id),
            provider_factory=lambda: provider,
            reclaim_after=timedelta(minutes=5),
        )
        report = await sweep.tick()
        assert report.considered == 0
        assert report.resumed == 0

    async def test_a_failure_resuming_one_charge_does_not_stop_the_rest(
        self,
        engines: None,
        session: AsyncSession,
        org_id: uuid.UUID,
        org_b: uuid.UUID,
        provider: RazorpayProvider,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """One organization's poisoned row is counted `failed`, not allowed to abort the whole
        pass — the same reasoning every other sweep in this platform states for the same bug."""

        async with session.begin():
            await services.stage_charge(
                session,
                provider,
                ChargeIn(
                    organization_id=org_id,
                    amount_minor=100,
                    currency="USD",
                    idempotency_key="ok-1",
                ),
            )

        async with session_scope(organization_id=org_b) as other:
            await services.stage_charge(
                other,
                provider,
                ChargeIn(
                    organization_id=org_b,
                    amount_minor=200,
                    currency="USD",
                    idempotency_key="ok-2",
                ),
            )

        import app.cron as cron_module

        original_call = services.call_provider_charge

        def _fail_for_first_org(provider_arg: object, charge: Charge) -> object:
            if charge.organization_id == org_id:
                raise RuntimeError("provider is unreachable")
            return original_call(provider_arg, charge)

        monkeypatch.setattr(cron_module.services, "call_provider_charge", _fail_for_first_org)

        sweep = StuckChargeSweep(
            discovery_factory=get_outbox_sessionmaker(),
            work_scope=lambda organization_id: session_scope(organization_id=organization_id),
            provider_factory=lambda: provider,
            reclaim_after=timedelta(seconds=0),
        )
        # Past the stager's lease: until it lapses, the charge's own call may still be in flight
        # and nobody else may make one (D3 round 3).
        report = await sweep.tick(now=datetime.now(UTC) + timedelta(minutes=10))

        assert report.considered == 2
        assert report.resumed == 1
        assert report.failed == 1
