"""D3 post-review: money-safety of the stuck-charge sweep's last step, and of the webhook.

Reproductions from the final review, turned into the tests that now hold the line:

* **A second debit.** At the attempt budget the sweep used to fail a charge locally and emit
  `commerce.payment.failed` without asking the provider — so when the last attempt's create had
  landed with its response lost, dunning debited again, and the provider's late
  `payment.captured` was refused (`failed -> captured`, a 422 that rolled the inbox back). Now the
  sweep makes a **lookup-only** call first and fails only if the provider holds nothing; and a
  capture for a charge the platform failed on its own judgement is recorded.
* **A captured charge with no provider id.** Only `finalize_charge` wrote `provider_payment_id`, so
  a call that outlived its timeout but completed left a charge the webhook captured with no id —
  no refund possible. The verified webhook now records it.
* **Two `commerce.payment.failed`.** The webhook read the charge unlocked and acted on a state the
  sweep had already changed. It now takes the same row lock.
* **A lease shorter than the call** is refused at start-up; **the Order receipt** is the charge id,
  which fits Razorpay's 40-character limit and is unique.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from pydantic import ValidationError
from sqlalchemy import select, text

from app import domain, services
from app.config import Settings, settings
from app.cron import StuckChargeSweep
from app.models import CHARGE_FAILED, AuditLog, Charge, OutboxEvent
from app.providers.razorpay import RazorpayProvider
from app.schemas import ChargeIn
from platform_core.db import get_outbox_sessionmaker, session_scope
from tests.helpers import razorpay_webhook_body

pytestmark = pytest.mark.integration


def _sweep(provider: RazorpayProvider) -> StuckChargeSweep:
    return StuckChargeSweep(
        discovery_factory=get_outbox_sessionmaker(),
        work_scope=lambda organization_id: session_scope(organization_id=organization_id),
        provider_factory=lambda: provider,
        reclaim_after=timedelta(minutes=5),
    )


def _charge_in(org_id: uuid.UUID, key: str) -> ChargeIn:
    return ChargeIn(organization_id=org_id, amount_minor=1000, currency="USD", idempotency_key=key)


async def _stage(provider: RazorpayProvider, org_id: uuid.UUID, key: str) -> Charge:
    async with session_scope(organization_id=org_id) as s:
        charge, _ = await services.stage_charge(s, provider, _charge_in(org_id, key))
    return charge


async def _read(org_id: uuid.UUID, charge_id: uuid.UUID) -> Charge:
    async with session_scope(organization_id=org_id) as s:
        return (await s.execute(select(Charge).where(Charge.id == charge_id))).scalar_one()


async def _events(org_id: uuid.UUID, charge_id: uuid.UUID) -> list[str]:
    async with session_scope(organization_id=org_id) as s:
        rows = await s.execute(
            select(OutboxEvent.event_type)
            .where(OutboxEvent.organization_id == org_id, OutboxEvent.aggregate_id == charge_id)
            .order_by(OutboxEvent.created_at)
        )
        return list(rows.scalars())


async def _audits(org_id: uuid.UUID, charge_id: uuid.UUID) -> list[str]:
    async with session_scope(organization_id=org_id) as s:
        rows = await s.execute(
            select(AuditLog.action).where(
                AuditLog.organization_id == org_id, AuditLog.entity_id == charge_id
            )
        )
        return list(rows.scalars())


async def _burn_budget(session, charge_id: uuid.UUID) -> None:
    """The call budget is spent and no call is in flight — where the final lookup starts."""
    async with session.begin():
        await session.execute(
            text(
                "UPDATE payment.charges SET attempts = :a, lease_until = NULL, "
                "next_attempt_at = NULL WHERE id = :i"
            ),
            {"a": settings().charge_max_attempts, "i": charge_id},
        )


async def _webhook(client: AsyncClient, provider: RazorpayProvider, body: bytes) -> Any:
    return await client.post(
        "/api/v1/webhooks/razorpay",
        content=body,
        headers={"X-Razorpay-Signature": provider.sign(body)},
    )


class TestTheFinalLookup:
    async def test_exhaustion_finalizes_a_charge_whose_last_attempt_made_a_payment(
        self, client, session, provider, org_id, engines, monkeypatch
    ) -> None:
        """The last attempt's create reached the provider and its answer was lost. The sweep
        looks before it fails: it finds the payment, records it, tells dunning nothing, and never
        creates a second one; the late capture then lands normally."""
        charge = await _stage(provider, org_id, "lost-resp-1")
        original = provider.charge
        creates = {"n": 0}
        max_attempts = settings().charge_max_attempts

        def flaky(**kwargs: Any) -> Any:
            creates["n"] += 1
            if creates["n"] < max_attempts - 1:
                raise RuntimeError("provider 503")
            original(**kwargs)  # the payment IS created at the provider ...
            raise TimeoutError("read timeout")  # ... and the answer never arrives

        monkeypatch.setattr(provider, "charge", flaky)
        sweep = _sweep(provider)
        base = datetime.now(UTC)
        for day in range(1, 8):
            await sweep.tick(now=base + timedelta(days=day))

        order = provider.find_order(receipt=str(charge.id))
        assert order is not None
        paid = provider.find_payment(order_id=order.provider_order_id, charge_id=str(charge.id))
        assert paid is not None
        stored = await _read(org_id, charge.id)
        assert stored.state == "authorized"
        assert stored.provider_payment_id == paid.provider_payment_id
        assert creates["n"] == max_attempts - 1, "a second debit was attempted"
        assert domain.EVT_FAILED not in await _events(org_id, charge.id)

        body = razorpay_webhook_body(
            event="payment.captured",
            provider_payment_id=paid.provider_payment_id,
            organization_id=org_id,
            charge_id=charge.id,
        )
        assert (await _webhook(client, provider, body)).status_code == 200
        assert (await _read(org_id, charge.id)).state == "captured"

    async def test_nothing_at_the_provider_fails_the_charge_once(
        self, session, provider, org_id, engines, monkeypatch
    ) -> None:
        charge = await _stage(provider, org_id, "none-found-1")
        await _burn_budget(session, charge.id)
        creates: list[str] = []
        monkeypatch.setattr(provider, "charge", lambda **kw: creates.append("create"))

        report = await _sweep(provider).tick(now=datetime.now(UTC) + timedelta(minutes=10))
        assert report.exhausted == 1
        assert creates == [], "the final lookup must never create"
        stored = await _read(org_id, charge.id)
        assert stored.state == CHARGE_FAILED
        assert stored.failure_reason == domain.FAILURE_ATTEMPTS_EXHAUSTED
        assert await _events(org_id, charge.id) == [domain.EVT_FAILED]

    async def test_a_lookup_that_cannot_be_made_is_retried_then_fails_as_lookup_unavailable(
        self, session, provider, org_id, engines, monkeypatch
    ) -> None:
        charge = await _stage(provider, org_id, "lookup-down-1")
        await _burn_budget(session, charge.id)
        lookups = {"n": 0}

        def down(**kwargs: Any) -> Any:
            lookups["n"] += 1
            raise RuntimeError("provider API down")

        monkeypatch.setattr(provider, "find_order", down)
        monkeypatch.setattr(provider, "charge", lambda **kw: pytest.fail("created during lookup"))
        sweep = _sweep(provider)
        at = datetime.now(UTC) + timedelta(minutes=10)
        for _ in range(settings().charge_final_lookup_attempts):
            report = await sweep.tick(now=at)
            assert report.failed == 1  # nothing known, nothing concluded
            assert (await _read(org_id, charge.id)).state == "created"
            at += domain.CHARGE_RETRY_CEILING + timedelta(minutes=10)
        assert lookups["n"] == settings().charge_final_lookup_attempts

        report = await sweep.tick(now=at)
        assert report.exhausted == 1
        stored = await _read(org_id, charge.id)
        assert stored.state == CHARGE_FAILED
        assert stored.failure_reason == domain.FAILURE_LOOKUP_UNAVAILABLE
        assert await _events(org_id, charge.id) == [domain.EVT_FAILED]

    async def test_a_lookup_that_times_out_concludes_nothing(
        self, session, provider, org_id, engines, monkeypatch
    ) -> None:
        charge = await _stage(provider, org_id, "lookup-slow-1")
        await _burn_budget(session, charge.id)
        monkeypatch.setattr(settings(), "provider_call_timeout_seconds", 0.2)
        original = provider.find_order

        def slow(**kwargs: Any) -> Any:
            time.sleep(1.0)
            return original(**kwargs)

        monkeypatch.setattr(provider, "find_order", slow)
        report = await _sweep(provider).tick(now=datetime.now(UTC) + timedelta(minutes=10))
        assert report.failed == 1
        stored = await _read(org_id, charge.id)
        assert stored.state == "created"
        assert await _events(org_id, charge.id) == []


class TestALateCaptureIsRecorded:
    async def test_a_capture_after_a_local_failure_is_recorded(
        self, client, session, provider, org_id, engines, monkeypatch
    ) -> None:
        charge = await _stage(provider, org_id, "late-cap-1")
        await _burn_budget(session, charge.id)
        await _sweep(provider).tick(now=datetime.now(UTC) + timedelta(minutes=10))
        assert (await _read(org_id, charge.id)).failure_reason == (
            domain.FAILURE_ATTEMPTS_EXHAUSTED
        )

        body = razorpay_webhook_body(
            event="payment.captured",
            provider_payment_id="pay_latecapture1",
            organization_id=org_id,
            charge_id=charge.id,
        )
        res = await _webhook(client, provider, body)
        assert res.status_code == 200, res.text
        stored = await _read(org_id, charge.id)
        assert stored.state == "captured"
        assert stored.provider_payment_id == "pay_latecapture1"
        assert stored.failure_reason is None
        assert await _events(org_id, charge.id) == [domain.EVT_FAILED, domain.EVT_CAPTURED]
        assert "payment.charge.captured_after_local_failure" in await _audits(org_id, charge.id)

    async def test_a_provider_failure_still_refuses_a_capture(
        self, client, session, provider, org_id, engines
    ) -> None:
        """Only the platform's own failures are provisional; the provider's are final."""
        charge = await _stage(provider, org_id, "prov-fail-1")
        failed = razorpay_webhook_body(
            event="payment.failed",
            provider_payment_id="pay_declined1",
            organization_id=org_id,
            charge_id=charge.id,
            error_description="card_declined",
        )
        assert (await _webhook(client, provider, failed)).status_code == 200
        captured = razorpay_webhook_body(
            event="payment.captured",
            provider_payment_id="pay_declined1",
            organization_id=org_id,
            charge_id=charge.id,
        )
        assert (await _webhook(client, provider, captured)).status_code == 422
        assert (await _read(org_id, charge.id)).state == CHARGE_FAILED


class TestTheWebhookRecordsTheProviderId:
    async def test_a_timed_out_call_whose_payment_captures_gets_its_ids(
        self, client, session, provider, org_id, engines, monkeypatch
    ) -> None:
        monkeypatch.setattr(settings(), "provider_call_timeout_seconds", 0.2)
        original = provider.charge

        def slow(**kwargs: Any) -> Any:
            time.sleep(0.5)
            return original(**kwargs)

        monkeypatch.setattr(provider, "charge", slow)
        charge = await _stage(provider, org_id, "tw-1")
        assert await services.call_provider_charge_off_loop(provider, charge) is None
        await asyncio.sleep(0.6)  # the abandoned thread completes the payment
        order = provider.find_order(receipt=str(charge.id))
        assert order is not None
        paid = provider.find_payment(order_id=order.provider_order_id, charge_id=str(charge.id))
        assert paid is not None

        body = razorpay_webhook_body(
            event="payment.captured",
            provider_payment_id=paid.provider_payment_id,
            organization_id=org_id,
            charge_id=charge.id,
            order_id=order.provider_order_id,
        )
        assert (await _webhook(client, provider, body)).status_code == 200
        stored = await _read(org_id, charge.id)
        assert stored.state == "captured"
        assert stored.provider_payment_id == paid.provider_payment_id
        assert stored.provider_order_id == order.provider_order_id
        assert (await _audits(org_id, charge.id)).count("payment.charge.created") == 1

        async with session_scope(organization_id=org_id) as s:
            locked = await s.get(Charge, charge.id)
            assert locked is not None
            refund = await services.create_refund(s, provider, locked, amount_minor=100)
        assert refund.provider_refund_id is not None

    async def test_a_different_id_is_audited_not_written(
        self, client, session, provider, org_id, engines
    ) -> None:
        charge = await _stage(provider, org_id, "mismatch-1")
        result = services.call_provider_charge(provider, charge)
        async with session_scope(organization_id=org_id) as s:
            await services.finalize_charge(s, charge.id, result)
        body = razorpay_webhook_body(
            event="payment.captured",
            provider_payment_id="pay_other",
            organization_id=org_id,
            charge_id=charge.id,
        )
        assert (await _webhook(client, provider, body)).status_code == 200
        assert (await _read(org_id, charge.id)).provider_payment_id == result.provider_payment_id
        assert "payment.charge.provider_id_mismatch" in await _audits(org_id, charge.id)


class TestTheWebhookAndTheSweepSerialize:
    async def _race(
        self,
        client: AsyncClient,
        provider: RazorpayProvider,
        org_id: uuid.UUID,
        charge: Charge,
        sweep_act: Any,
        body: bytes,
    ) -> int:
        started, release = asyncio.Event(), asyncio.Event()

        async def hold() -> None:
            async with session_scope(organization_id=org_id) as s:
                await sweep_act(s)
                started.set()
                await release.wait()

        held = asyncio.create_task(hold())
        await asyncio.wait(
            {held, asyncio.ensure_future(started.wait())},
            timeout=10,
            return_when=asyncio.FIRST_COMPLETED,
        )
        if held.done():
            held.result()  # the sweep's act raised before it held the lock: surface why
        assert started.is_set(), "the sweep's act never took the row lock"
        webhook = asyncio.create_task(_webhook(client, provider, body))
        await asyncio.sleep(0.5)
        assert not webhook.done(), "the webhook did not wait for the sweep's row lock"
        release.set()
        await held
        return (await webhook).status_code

    async def test_webhook_failed_vs_exhaustion_race_double_failed(
        self, client, session, provider, org_id, engines
    ) -> None:
        """The review's race: the sweep holds the charge at the budget while a `payment.failed`
        arrives. Exactly one `commerce.payment.failed`."""
        charge = await _stage(provider, org_id, "race-exh-2")
        await _burn_budget(session, charge.id)

        async def claim(s: Any) -> None:
            await services.claim_stuck_charge(s, charge.id, now=datetime.now(UTC))

        body = razorpay_webhook_body(
            event="payment.failed",
            provider_payment_id="pay_x2",
            organization_id=org_id,
            charge_id=charge.id,
        )
        assert await self._race(client, provider, org_id, charge, claim, body) == 200
        assert (await _events(org_id, charge.id)).count(domain.EVT_FAILED) == 1

        # And the sweep's own none-found step, run after, does not fail it a second time.
        async with session_scope(organization_id=org_id) as s:
            assert await services.fail_exhausted_charge(s, charge.id) is None
        assert (await _events(org_id, charge.id)).count(domain.EVT_FAILED) == 1

    async def test_a_capture_racing_the_sweeps_failure_is_recorded_after_it(
        self, client, session, provider, org_id, engines
    ) -> None:
        charge = await _stage(provider, org_id, "race-exh-3")

        async def fail(s: Any) -> None:
            assert await services.fail_exhausted_charge(s, charge.id) is not None

        body = razorpay_webhook_body(
            event="payment.captured",
            provider_payment_id="pay_x3",
            organization_id=org_id,
            charge_id=charge.id,
        )
        assert await self._race(client, provider, org_id, charge, fail, body) == 200
        assert (await _read(org_id, charge.id)).state == "captured"
        assert await _events(org_id, charge.id) == [domain.EVT_FAILED, domain.EVT_CAPTURED]


class TestTheLeaseOutlivesTheCall:
    def test_a_lease_shorter_than_twice_the_call_is_refused(self) -> None:
        """The review's hung-thread double create: the abandoned call outlived its lease and its
        late create landed after the sweep's lookup found nothing. A configuration that allows
        that no longer starts."""
        with pytest.raises(ValidationError, match="charge_reclaim_after_seconds"):
            Settings(charge_reclaim_after_seconds=100, provider_call_timeout_seconds=30)
        with pytest.raises(ValidationError, match="charge_reclaim_after_seconds"):
            Settings(charge_reclaim_after_seconds=300, provider_call_timeout_seconds=200)
        ok = Settings(charge_reclaim_after_seconds=120, provider_call_timeout_seconds=30)
        assert ok.charge_reclaim_after_seconds == 120

    def test_the_defaults_satisfy_it(self) -> None:
        cfg = settings()
        assert cfg.charge_reclaim_after_seconds >= 2 * cfg.provider_call_timeout_seconds + 60


class TestTheOrderReceipt:
    async def test_the_receipt_is_the_charge_id(
        self, provider: RazorpayProvider, org_id, engines
    ) -> None:
        """Razorpay caps `receipt` at 40 characters and does not make it unique: the platform key
        `invoice:<uuid>` is 44. The charge's own id is 36, unique and stable."""
        key = f"invoice:{uuid.uuid4()}"
        assert len(key) > 40
        charge = await _stage(provider, org_id, key)
        result = services.call_provider_charge(provider, charge)
        order = provider.find_order(receipt=str(charge.id))
        assert order is not None and len(order.receipt) <= 40
        assert result.provider_order_id == order.provider_order_id
        assert provider.find_order(receipt=key) is None
