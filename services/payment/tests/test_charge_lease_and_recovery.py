"""D3 round 3: who may call the provider for a charge, and what recording its answer may change.

Each class is a reproduction a review found against the round-2 split, turned into the test that
now holds the line:

* a **webhook landing before `finalize_charge`** had its `captured` regressed to `authorized`
  (finalize overwrote state and provider id unconditionally);
* a caller's **HTTP retry of a key whose first call was still in flight** called the provider
  again (`needs_call` was true for any charge without a provider payment id);
* a **provider that always raises** was re-driven by the sweep every tick, forever, and the charge
  sat `created` — dunning never heard;
* the sweep's discovery role could **read every column** of `charges`.

And the contract that makes a resumed call safe — **lookup before create** against a provider
Order — is exercised through the stub adapter, which keeps the Orders and payments a real account
would.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from app import domain, selectors, services
from app.config import settings
from app.cron import StuckChargeSweep
from app.models import CHARGE_CREATED, CHARGE_FAILED, AuditLog, Charge, OutboxEvent
from app.providers.base import ChargeResult
from app.providers.razorpay import RazorpayProvider
from app.schemas import ChargeIn
from platform_core.db import get_outbox_sessionmaker, session_scope
from tests.helpers import razorpay_webhook_body, service_token

pytestmark = pytest.mark.integration

LATER = timedelta(minutes=10)


def _sweep(provider: RazorpayProvider) -> StuckChargeSweep:
    return StuckChargeSweep(
        discovery_factory=get_outbox_sessionmaker(),
        work_scope=lambda organization_id: session_scope(organization_id=organization_id),
        provider_factory=lambda: provider,
        reclaim_after=timedelta(minutes=5),
    )


def _charge_in(org_id: uuid.UUID, key: str) -> ChargeIn:
    return ChargeIn(organization_id=org_id, amount_minor=1000, currency="USD", idempotency_key=key)


async def _read(org_id: uuid.UUID, charge_id: uuid.UUID) -> Charge:
    async with session_scope(organization_id=org_id) as s:
        return (await s.execute(select(Charge).where(Charge.id == charge_id))).scalar_one()


async def _audit_actions(org_id: uuid.UUID, charge_id: uuid.UUID) -> list[str]:
    async with session_scope(organization_id=org_id) as s:
        rows = (
            await s.execute(
                select(AuditLog.action)
                .where(AuditLog.organization_id == org_id, AuditLog.entity_id == charge_id)
                .order_by(AuditLog.id)
            )
        ).scalars()
        return list(rows)


def _count_creates(provider: RazorpayProvider, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []
    original = provider.charge

    def _spy(**kwargs: Any) -> Any:
        calls.append(kwargs["idempotency_key"])
        return original(**kwargs)

    monkeypatch.setattr(provider, "charge", _spy)
    return calls


class TestAWebhookIsNeverRegressed:
    async def test_a_capture_that_lands_before_finalize_stays_captured(
        self, client: AsyncClient, session, provider: RazorpayProvider, org_id, engines
    ) -> None:
        async with session_scope(organization_id=org_id) as s:
            charge, needs_call = await services.stage_charge(
                s, provider, _charge_in(org_id, "race-1")
            )
        assert needs_call
        result = services.call_provider_charge(provider, charge)

        body = razorpay_webhook_body(
            event="payment.captured",
            provider_payment_id=result.provider_payment_id,
            organization_id=org_id,
            charge_id=charge.id,
        )
        res = await client.post(
            "/api/v1/webhooks/razorpay",
            content=body,
            headers={"X-Razorpay-Signature": provider.sign(body)},
        )
        assert res.status_code == 200, res.text
        assert (await _read(org_id, charge.id)).state == "captured"

        async with session_scope(organization_id=org_id) as s:
            await services.finalize_charge(s, charge.id, result)
        stored = await _read(org_id, charge.id)
        assert stored.state == "captured"
        assert stored.provider_payment_id == result.provider_payment_id
        assert stored.provider_order_id == result.provider_order_id
        assert stored.lease_until is None

        actions = await _audit_actions(org_id, charge.id)
        assert actions.count("payment.charge.staged") == 1
        assert actions.count("payment.charge.created") == 1

    async def test_a_second_recorder_neither_overwrites_nor_re_audits(
        self, provider: RazorpayProvider, org_id, engines
    ) -> None:
        async with session_scope(organization_id=org_id) as s:
            charge, _ = await services.stage_charge(s, provider, _charge_in(org_id, "race-2"))
        result = services.call_provider_charge(provider, charge)
        async with session_scope(organization_id=org_id) as s:
            await services.finalize_charge(s, charge.id, result)

        different = ChargeResult(
            provider="razorpay",
            provider_payment_id="pay_somethingelse",
            state="authorized",
            amount_minor=1000,
            currency="USD",
        )
        async with session_scope(organization_id=org_id) as s:
            await services.finalize_charge(s, charge.id, different)
        stored = await _read(org_id, charge.id)
        assert stored.provider_payment_id == result.provider_payment_id
        actions = await _audit_actions(org_id, charge.id)
        assert actions.count("payment.charge.created") == 1
        assert actions.count("payment.charge.provider_id_mismatch") == 1


class TestOneCallAtATime:
    async def test_a_retry_of_an_in_flight_key_does_not_call_the_provider(
        self, client: AsyncClient, session, provider: RazorpayProvider, org_id, engines, monkeypatch
    ) -> None:
        creates = _count_creates(provider, monkeypatch)
        # Request A has staged and committed, and is out at the provider.
        async with session_scope(organization_id=org_id) as s:
            charge, needs_call = await services.stage_charge(
                s, provider, _charge_in(org_id, "dup-1")
            )
        assert needs_call
        # Request B: the caller's HTTP retry of the same key.
        res = await client.post(
            "/internal/v1/charges",
            headers={"X-Internal-Token": service_token()},
            json={
                "organization_id": str(org_id),
                "amount_minor": 1000,
                "currency": "USD",
                "idempotency_key": "dup-1",
            },
        )
        assert res.status_code == 201, res.text
        assert res.json()["id"] == str(charge.id)
        assert res.json()["state"] == CHARGE_CREATED
        assert creates == []

    async def test_the_lease_is_claimed_by_one_caller_only(
        self, provider: RazorpayProvider, org_id, engines
    ) -> None:
        async with session_scope(organization_id=org_id) as s:
            charge, _ = await services.stage_charge(s, provider, _charge_in(org_id, "lease-1"))
        later = datetime.now(UTC) + LATER
        async with session_scope(organization_id=org_id) as s:
            first = await services.claim_charge_lease(s, charge.id, now=later)
        async with session_scope(organization_id=org_id) as s:
            second = await services.claim_charge_lease(s, charge.id, now=later)
        assert first is not None and first.attempts == 2
        assert second is None


class TestLookupBeforeCreate:
    async def test_a_resumed_call_finds_the_payment_instead_of_creating_one(
        self, session, provider: RazorpayProvider, org_id, engines, monkeypatch
    ) -> None:
        """Stage, call (the provider makes the payment), crash before recording. The sweep, past
        the lease, calls again — and finds the payment by the charge's Order, so the provider is
        asked to create exactly once."""
        creates = _count_creates(provider, monkeypatch)
        async with session_scope(organization_id=org_id) as s:
            charge, _ = await services.stage_charge(s, provider, _charge_in(org_id, "crash-1"))
        crashed = services.call_provider_charge(provider, charge)
        assert creates == ["crash-1"]

        report = await _sweep(provider).tick(now=datetime.now(UTC) + LATER)
        assert report.resumed == 1 and report.failed == 0
        assert creates == ["crash-1"]
        stored = await _read(org_id, charge.id)
        assert stored.provider_payment_id == crashed.provider_payment_id
        assert stored.provider_order_id == crashed.provider_order_id
        assert stored.state == "authorized"

    def test_the_order_is_found_by_its_receipt(self, provider: RazorpayProvider) -> None:
        first = provider.ensure_order(receipt="k-1", amount_minor=1, currency="USD", notes={})
        again = provider.ensure_order(receipt="k-1", amount_minor=1, currency="USD", notes={})
        other = provider.ensure_order(receipt="k-2", amount_minor=1, currency="USD", notes={})
        assert first == again
        assert other.provider_order_id != first.provider_order_id
        assert provider.find_payment(order_id=first.provider_order_id, charge_id="c") is None


class TestTheSweepIsBounded:
    async def test_a_provider_that_always_raises_fails_the_charge_and_tells_dunning(
        self, session, provider: RazorpayProvider, org_id, engines, monkeypatch
    ) -> None:
        async with session_scope(organization_id=org_id) as s:
            charge, _ = await services.stage_charge(s, provider, _charge_in(org_id, "poison-1"))
        calls = {"n": 0}

        def _boom(**kwargs: Any) -> Any:
            calls["n"] += 1
            raise RuntimeError("card declined permanently")

        monkeypatch.setattr(provider, "charge", _boom)
        sweep = _sweep(provider)
        base = datetime.now(UTC)
        for day in range(1, 8):
            await sweep.tick(now=base + timedelta(days=day))

        max_attempts = settings().charge_max_attempts
        # The stager's own attempt counts; the sweep made the rest, then stopped creating — its
        # last act was a lookup (one more claimed attempt), which found no payment.
        assert calls["n"] == max_attempts - 1
        stored = await _read(org_id, charge.id)
        assert stored.state == CHARGE_FAILED
        assert stored.failure_reason == domain.FAILURE_ATTEMPTS_EXHAUSTED
        assert stored.attempts == max_attempts + 1
        async with session_scope(organization_id=org_id) as s:
            events = (
                await s.execute(
                    select(OutboxEvent.event_type).where(
                        OutboxEvent.organization_id == org_id,
                        OutboxEvent.aggregate_id == charge.id,
                    )
                )
            ).scalars()
            assert list(events) == [domain.EVT_FAILED]

    async def test_a_charge_backing_off_is_not_discovered(
        self, provider: RazorpayProvider, org_id, engines, monkeypatch
    ) -> None:
        async with session_scope(organization_id=org_id) as s:
            await services.stage_charge(s, provider, _charge_in(org_id, "backoff-1"))

        def _boom(**kwargs: Any) -> Any:
            raise RuntimeError("down")

        monkeypatch.setattr(provider, "charge", _boom)
        sweep = _sweep(provider)
        first = datetime.now(UTC) + LATER
        assert (await sweep.tick(now=first)).considered == 1
        # The claim set `next_attempt_at` a backoff out; inside it the charge is not even
        # discovered, so it cannot occupy a batch slot another organization's charge needs.
        assert (await sweep.tick(now=first + timedelta(minutes=1))).considered == 0
        later = first + domain.charge_retry_backoff(2) + timedelta(seconds=1)
        assert (await sweep.tick(now=later)).considered == 1


class TestDiscoveryIsColumnScoped:
    async def test_the_outbox_role_holds_exactly_the_discovery_columns(self, admin_session) -> None:
        async with admin_session.begin():
            rows = (
                await admin_session.execute(
                    text(
                        "SELECT column_name FROM information_schema.column_privileges "
                        "WHERE grantee = :r AND table_schema = 'payment' "
                        "AND table_name = 'charges' AND privilege_type = 'SELECT'"
                    ),
                    {"r": settings().db_outbox_role},
                )
            ).scalars()
            granted = set(rows)
            table_level = (
                await admin_session.execute(
                    text(
                        "SELECT privilege_type FROM information_schema.table_privileges "
                        "WHERE grantee = :r AND table_schema = 'payment' "
                        "AND table_name = 'charges'"
                    ),
                    {"r": settings().db_outbox_role},
                )
            ).scalars()
            assert list(table_level) == []
        assert granted == set(selectors.SWEEP_READER_CHARGE_COLUMNS)

    @pytest.mark.parametrize("column", ["idempotency_key", "amount_minor", "failure_reason"])
    async def test_content_columns_are_refused(self, column: str, engines) -> None:
        async with get_outbox_sessionmaker()() as s:
            with pytest.raises(DBAPIError, match="permission denied"):
                async with s.begin():
                    # `column` comes from the parametrize list above, never from input.
                    await s.execute(text(f"SELECT {column} FROM payment.charges LIMIT 1"))  # noqa: S608
