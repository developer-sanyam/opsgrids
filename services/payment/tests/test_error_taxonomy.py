"""The error taxonomy at the seam (ADR-0032): every way a provider call can end is one of four
outcomes, and the service layer treats them differently.

* **result** — recorded.
* **unknown** (a timeout, a connect failure, a `429`/`5xx`) — nothing recorded, nothing concluded;
  the charge stays `created` under its lease and the sweep's lookup-before-create settles it.
* **rejected** (any other `4xx`) — provably nothing created: the charge **fails**, with the
  provider's stable code as its reason, and dunning proceeds.
* **misconfigured** (a `401`/`403`, or Razorpay's invalid-key answer) — the platform's own bad key:
  **never the charge's fault**. Not failed, not announced, the claimed attempt given back, the
  provider not called again until the latch lapses, an operator paged once.

Before this, `RazorpayApiError` and raw `httpx` errors propagated, `call_provider_charge_off_loop`
caught only `TimeoutError`, and a bad key burned all five attempts of every charge and then failed
it — `commerce.payment.failed`, dunning, suspension of paying tenants for the platform's own typo.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy import select
from structlog.testing import capture_logs

from app import services
from app.config import settings
from app.cron import StuckChargeSweep
from app.models import AuditLog, Charge, OutboxEvent
from app.providers.base import ChargeResult, ProviderCallError
from app.providers.razorpay import RazorpayProvider
from app.providers.wire import (
    CREDENTIALS,
    CREDENTIALS_LATCH_SECONDS,
    ProviderMisconfigured,
    ProviderRejected,
    ProviderTransient,
    current_deadline,
)
from app.schemas import ChargeIn
from platform_core.db import get_outbox_sessionmaker, session_scope
from tests.helpers import service_token

pytestmark = pytest.mark.integration


class Scripted(RazorpayProvider):
    """The Razorpay stub whose Order lookup raises (or doesn't), counting every call."""

    def __init__(self, error: BaseException | None = None) -> None:
        super().__init__(key_id=None, key_secret=None, webhook_secret="dev-only-taxonomy-hook")
        self.error = error
        self.calls = 0
        self.deadlines: list[float | None] = []

    def ensure_order(self, **kwargs: Any) -> Any:
        self.calls += 1
        deadline = current_deadline()
        self.deadlines.append(deadline.remaining() if deadline is not None else None)
        if self.error is not None:
            raise self.error
        return super().ensure_order(**kwargs)


async def _stage(org_id: uuid.UUID, provider: RazorpayProvider, key: str) -> Charge:
    payload = ChargeIn(
        organization_id=org_id, amount_minor=1000, currency="INR", idempotency_key=key
    )
    async with session_scope(organization_id=org_id) as s:
        charge, needs_call = await services.stage_charge(s, provider, payload)
    assert needs_call and charge.attempts == 1
    return charge


async def _read(org_id: uuid.UUID, charge_id: uuid.UUID) -> Charge:
    async with session_scope(organization_id=org_id) as s:
        return (await s.execute(select(Charge).where(Charge.id == charge_id))).scalar_one()


async def _events(org_id: uuid.UUID, charge_id: uuid.UUID) -> list[str]:
    async with session_scope(organization_id=org_id) as s:
        found = await s.execute(
            select(OutboxEvent.event_type).where(
                OutboxEvent.organization_id == org_id, OutboxEvent.aggregate_id == charge_id
            )
        )
        return list(found.scalars())


async def _record(
    org_id: uuid.UUID, charge: Charge, outcome: services.CallOutcome
) -> Charge | None:
    async with session_scope(organization_id=org_id) as s:
        return await services.record_charge_outcome(s, charge.id, outcome, organization_id=org_id)


class TestClassification:
    @pytest.mark.parametrize(
        ("error", "kind", "code"),
        [
            (
                ProviderRejected("razorpay", 400, "BAD_REQUEST_ERROR"),
                "rejected",
                "BAD_REQUEST_ERROR",
            ),
            (ProviderRejected("razorpay", 404, None), "rejected", "http_404"),
            (
                ProviderMisconfigured("razorpay", 401, "BAD_REQUEST_ERROR"),
                "misconfigured",
                "BAD_REQUEST_ERROR",
            ),
            (ProviderTransient("razorpay", 503, None), "unknown", None),
            (ProviderTransient("razorpay", 0, "timeout"), "unknown", "timeout"),
            (ProviderCallError("razorpay created an order and returned no id"), "unknown", None),
            (httpx.ConnectError("refused"), "unknown", None),
            (TimeoutError(), "unknown", "timeout"),
            (
                NotImplementedError("charge is not available on the live wire"),
                "rejected",
                "debit_not_supported",
            ),
        ],
    )
    async def test_every_way_a_call_can_end_is_one_outcome(
        self,
        org_id: uuid.UUID,
        session: Any,
        engines: None,
        error: BaseException,
        kind: str,
        code: str | None,
    ) -> None:
        provider = Scripted(error)
        charge = await _stage(org_id, provider, f"k-{uuid.uuid4()}")
        outcome = await services.attempt_provider_charge(provider, charge)
        assert outcome.kind == kind
        assert outcome.code == code
        assert outcome.result is None
        assert outcome.needs_record is (kind != "unknown")

    async def test_a_defect_is_not_dressed_up_as_an_outcome(
        self, org_id: uuid.UUID, session: Any, engines: None
    ) -> None:
        provider = Scripted(ValueError("a bug"))
        charge = await _stage(org_id, provider, "k-bug")
        with pytest.raises(ValueError, match="a bug"):
            await services.attempt_provider_charge(provider, charge)

    async def test_an_answer_is_a_result(
        self, org_id: uuid.UUID, session: Any, engines: None
    ) -> None:
        provider = Scripted()
        charge = await _stage(org_id, provider, "k-ok")
        outcome = await services.attempt_provider_charge(provider, charge)
        assert outcome.kind == "result" and isinstance(outcome.result, ChargeResult)
        assert await services.call_provider_charge_off_loop(provider, charge) is not None

    async def test_the_call_runs_under_an_overall_deadline_of_the_call_timeout(
        self, org_id: uuid.UUID, session: Any, engines: None
    ) -> None:
        provider = Scripted()
        charge = await _stage(org_id, provider, "k-deadline")
        await services.attempt_provider_charge(provider, charge)
        (remaining,) = provider.deadlines
        assert remaining is not None
        assert 0 < remaining <= settings().provider_call_timeout_seconds
        # Called directly (a test, a script), it is bounded all the same.
        provider.deadlines.clear()
        services.call_provider_charge(provider, charge)
        assert provider.deadlines[0] is not None

    async def test_a_timeout_is_unknown_and_the_old_wrapper_still_says_none(
        self, org_id: uuid.UUID, session: Any, engines: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        provider = Scripted()
        charge = await _stage(org_id, provider, "k-slow")
        monkeypatch.setattr(settings(), "provider_call_timeout_seconds", 0.05)

        def slow(*args: Any, **kwargs: Any) -> Any:
            import time

            time.sleep(0.4)

        monkeypatch.setattr(provider, "ensure_order", slow)
        assert (await services.attempt_provider_charge(provider, charge)).kind == "unknown"
        assert await services.call_provider_charge_off_loop(provider, charge) is None


class TestRecording:
    async def test_a_rejection_fails_the_charge_with_the_providers_code_and_dunning_hears(
        self, org_id: uuid.UUID, session: Any, engines: None
    ) -> None:
        provider = Scripted(ProviderRejected("razorpay", 400, "BAD_REQUEST_ERROR"))
        charge = await _stage(org_id, provider, "k-rejected")
        outcome = await services.attempt_provider_charge(provider, charge)
        failed = await _record(org_id, charge, outcome)
        assert failed is not None and failed.state == "failed"
        row = await _read(org_id, charge.id)
        assert row.failure_reason == "provider_rejected:BAD_REQUEST_ERROR"
        assert row.lease_until is None and row.provider_payment_id is None
        assert await _events(org_id, charge.id) == ["commerce.payment.failed"]
        async with session_scope(organization_id=org_id) as s:
            audits = (
                (await s.execute(select(AuditLog).where(AuditLog.entity_id == charge.id)))
                .scalars()
                .all()
            )
        assert ("payment.charge.failed", "billing") in {(a.action, a.acting_as) for a in audits}

    async def test_a_rejected_key_gives_the_attempt_back_fails_nothing_and_pages_once(
        self, org_id: uuid.UUID, session: Any, engines: None
    ) -> None:
        provider = Scripted(ProviderMisconfigured("razorpay", 401, "BAD_REQUEST_ERROR"))
        first = await _stage(org_id, provider, "k-key-1")
        second = await _stage(org_id, provider, "k-key-2")

        with capture_logs() as logs:
            one = await services.attempt_provider_charge(provider, first)
            await _record(org_id, first, one)
            two = await services.attempt_provider_charge(provider, second)  # latched: no call
            await _record(org_id, second, two)

        assert (one.kind, two.kind) == ("misconfigured", "misconfigured")
        assert provider.calls == 1, "the latch must stop the provider being asked again"
        for charge in (first, second):
            row = await _read(org_id, charge.id)
            assert row.state == "created" and row.failure_reason is None
            assert row.attempts == 0, "the claimed attempt is given back"
            assert row.lease_until is None
            assert row.next_attempt_at is not None
            assert row.next_attempt_at >= datetime.now(UTC) + timedelta(
                seconds=CREDENTIALS_LATCH_SECONDS - 60
            )
            assert await _events(org_id, charge.id) == [], "a bad key must not announce a failure"
        pages = [line for line in logs if line["event"] == "payment.provider.credentials_rejected"]
        assert len(pages) == 1 and pages[0]["log_level"] == "error"
        assert CREDENTIALS.tripped("razorpay")

    async def test_an_unknown_outcome_records_nothing(
        self, org_id: uuid.UUID, session: Any, engines: None
    ) -> None:
        provider = Scripted(ProviderTransient("razorpay", 503, None))
        charge = await _stage(org_id, provider, "k-unknown")
        outcome = await services.attempt_provider_charge(provider, charge)
        await _record(org_id, charge, outcome)
        row = await _read(org_id, charge.id)
        assert (row.state, row.attempts) == ("created", 1)
        assert row.lease_until is not None  # still held: the lease lapses into the sweep
        assert await _events(org_id, charge.id) == []

    async def test_a_charge_something_else_resolved_is_left_alone(
        self, org_id: uuid.UUID, session: Any, engines: None
    ) -> None:
        provider = Scripted(ProviderRejected("razorpay", 400, "X"))
        charge = await _stage(org_id, provider, "k-resolved")
        outcome = await services.attempt_provider_charge(provider, charge)
        async with session_scope(organization_id=org_id) as s:
            locked = await s.get(Charge, charge.id)
            assert locked is not None
            locked.state = "captured"
            locked.provider_payment_id = "pay_elsewhere"
        assert await _record(org_id, charge, outcome) is None
        assert (await _read(org_id, charge.id)).state == "captured"


class TestTheCallers:
    async def test_the_sweep_fails_a_rejected_charge_and_gives_back_a_rejected_key(
        self, org_id: uuid.UUID, session: Any, engines: None
    ) -> None:
        rejected, misconfigured = (
            Scripted(ProviderRejected("razorpay", 400, "BAD_REQUEST_ERROR")),
            Scripted(ProviderMisconfigured("razorpay", 401, None)),
        )
        a = await _stage(org_id, rejected, "k-sweep-a")
        later = datetime.now(UTC) + timedelta(minutes=10)

        def sweep(provider: RazorpayProvider) -> StuckChargeSweep:
            return StuckChargeSweep(
                discovery_factory=get_outbox_sessionmaker(),
                work_scope=lambda organization_id: session_scope(organization_id=organization_id),
                provider_factory=lambda: provider,
                reclaim_after=timedelta(minutes=5),
            )

        report = await sweep(rejected).tick(now=later)
        assert (report.rejected, report.resumed, report.failed) == (1, 0, 0)
        assert (await _read(org_id, a.id)).failure_reason == "provider_rejected:BAD_REQUEST_ERROR"
        assert await _events(org_id, a.id) == ["commerce.payment.failed"]

        b = await _stage(org_id, misconfigured, "k-sweep-b")
        report = await sweep(misconfigured).tick(now=later)
        assert report.misconfigured == 1 and report.rejected == 0
        row = await _read(org_id, b.id)
        # The stager's attempt stands; the sweep's own claim was given back.
        assert row.state == "created" and row.attempts == 1
        assert await _events(org_id, b.id) == []

    async def test_the_internal_route_answers_201_with_the_charge_as_it_stands(
        self, client: AsyncClient, session: Any, org_id: uuid.UUID, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from tests import checkout_support as cs

        provider = cs.install(Scripted(ProviderMisconfigured("razorpay", 401, None)))
        response = await client.post(
            "/internal/v1/charges",
            headers={"X-Internal-Token": service_token()},
            json={
                "organization_id": str(org_id),
                "amount_minor": 1000,
                "currency": "INR",
                "idempotency_key": "k-route",
            },
        )
        assert response.status_code == 201, response.text
        assert response.json()["state"] == "created"
        assert provider.calls == 1  # type: ignore[attr-defined]

        CREDENTIALS.reset()  # the first call latched the key; a rejection is a different outcome
        rejected = cs.install(Scripted(ProviderRejected("razorpay", 400, "BAD_REQUEST_ERROR")))
        response = await client.post(
            "/internal/v1/charges",
            headers={"X-Internal-Token": service_token()},
            json={
                "organization_id": str(org_id),
                "amount_minor": 1000,
                "currency": "INR",
                "idempotency_key": "k-route-2",
            },
        )
        assert response.status_code == 201 and response.json()["state"] == "failed"
        assert rejected.calls == 1  # type: ignore[attr-defined]


class TestFinalizeRefusesAnotherProvidersAnswer:
    async def test_a_result_for_another_provider_is_refused_and_records_nothing(
        self, org_id: uuid.UUID, session: Any, engines: None
    ) -> None:
        from platform_core.errors import BusinessRuleProblem

        provider = Scripted()
        charge = await _stage(org_id, provider, "k-mismatch")
        foreign = ChargeResult(
            provider="cashfree",
            provider_payment_id="9999",
            state="authorized",
            amount_minor=1000,
            currency="INR",
        )
        async with session_scope(organization_id=org_id) as s:
            with pytest.raises(BusinessRuleProblem):
                await services.finalize_charge(s, charge.id, foreign, organization_id=org_id)
        row = await _read(org_id, charge.id)
        assert row.provider_payment_id is None and row.state == "created"
