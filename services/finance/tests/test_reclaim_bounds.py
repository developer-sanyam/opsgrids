"""D3 round 3: the filing-reclaim sweep is bounded, and nothing it or a request records can land a
stale fact on top of a newer one.

Each class here is a reproduction a review found against the round-2 sweep, turned into the test
that now holds the line:

* an organization with **no registration** had its invoices re-queued every lease, forever — the
  consumer answers `no_registration` and writes no row, so "no filing row" stayed true;
* a consumer that **never answers** (an adapter raising outside the three mapped exceptions) was
  re-queued forever, at attempt 1 every time;
* discovery took the **oldest 100** such invoices and skipped them under the tenant binding, so a
  handful of them blocked every other organization's lost invoice;
* two drivers **registering the same document** (the manual route and the consumer) both reached
  the record step, and the second hit `uq_einvoice_filings_org_irn` — or appended a late outage
  `pending` on top of the `filed` row;
* an e-Way generation chain was re-driven with **no age cap**, and a withdrawal re-driven past
  the portal's **24-hour window** — both now end in a terminal row with no portal call.
"""

from __future__ import annotations

import contextlib
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select, text

from app import domain, selectors, services
from app.consumers import file_requested_invoice
from app.eway.base import EwayPayload, EwayUnavailable
from app.eway.stub import StubEwayClient
from app.irp.stub import StubIrpClient
from app.models import Invoice, OutboxEvent
from app.schemas import (
    CancelEwayIn,
    CancelFilingIn,
    CustomerCreate,
    FileInvoiceIn,
    GstRegistrationUpsert,
    InvoiceCreate,
    InvoiceLineCreate,
)
from platform_core.db import session_scope
from tests.helpers import admin_ctx
from tests.test_compliance_transaction_boundary import (  # noqa: F401 - `_engine` is an autouse fixture
    BUYER_SAME_STATE,
    LATER,
    SELLER,
    _bills,
    _consignment,
    _engine,
    _events,
    _filed,
    _filings,
    _generate_in,
    _sweep,
)

pytestmark = pytest.mark.integration


async def _invoice(org_id: uuid.UUID, number: str, *, registered: bool) -> Invoice:
    ctx = admin_ctx(org_id)
    async with session_scope(organization_id=org_id) as s:
        if registered:
            await services.set_gst_registration(
                s, ctx, GstRegistrationUpsert(gstin=SELLER, legal_name="Seller Pvt Ltd")
            )
        customer = await services.create_customer(
            s,
            ctx,
            CustomerCreate(
                name="Buyer",
                contact_name="C",
                email="b@example.in",
                gstin=BUYER_SAME_STATE,
                city="Pune",
                state="Maharashtra",
            ),
        )
        return await services.create_invoice(
            s,
            ctx,
            InvoiceCreate(
                number=number,
                customer_id=customer.id,
                issued_date=date(2026, 8, 12),
                due_date=date(2026, 9, 11),
                tax_rate_pct=18,
                lines=[
                    InvoiceLineCreate(
                        description="Consulting",
                        quantity=1,
                        unit_price_minor=1000_00,
                        hsn_sac="998314",
                    )
                ],
            ),
        )


async def _lose_finalize_request(admin_session, org_id: uuid.UUID) -> None:
    """The broker lost the finalize-time `filing_requested`: nothing will ever file it but the
    sweep."""
    async with admin_session.begin():
        await admin_session.execute(
            text("DELETE FROM finance.outbox WHERE organization_id = :o"), {"o": org_id}
        )


async def _requests(org_id: uuid.UUID) -> list[OutboxEvent]:
    async with session_scope(organization_id=org_id) as s:
        stmt = (
            select(OutboxEvent)
            .where(
                OutboxEvent.organization_id == org_id,
                OutboxEvent.event_type == services.EVT_INVOICE_FILING_REQUESTED,
            )
            .order_by(OutboxEvent.created_at)
        )
        return list((await s.execute(stmt)).scalars().all())


async def _publish_all(admin_session, org_id: uuid.UUID) -> None:
    """The relay publishes everything staged — only a published request is an attempt."""
    async with admin_session.begin():
        await admin_session.execute(
            text(
                "UPDATE finance.outbox SET published_at = now() "
                "WHERE organization_id = :o AND published_at IS NULL"
            ),
            {"o": org_id},
        )


async def _deliver_all(org_id: uuid.UUID) -> None:
    """Run the consumer on every staged `filing_requested`, as the broker would."""
    for row in await _requests(org_id):
        # RuntimeError: the poisoned adapter below; its claim already marked the message processed.
        with contextlib.suppress(RuntimeError):
            await file_requested_invoice(
                {
                    "event_id": str(row.id),
                    "event_type": row.event_type,
                    "organization_id": str(row.organization_id),
                    "payload": row.payload,
                }
            )


async def _age(admin_session, org_id: uuid.UUID) -> None:
    """An hour passes on the database's clock: outbox rows and invoices move back together, so
    their order — which the request count depends on — is kept."""
    async with admin_session.begin():
        for table in ("outbox", "invoices"):
            await admin_session.execute(
                text(
                    f"UPDATE finance.{table} SET created_at = created_at - interval '1 hour' "  # noqa: S608
                    "WHERE organization_id = :o"
                ),
                {"o": org_id},
            )


class TestNoRegistrationIsNeverRequeued:
    async def test_four_ticks_requeue_nothing(self, session, admin_session, org_id) -> None:
        invoice = await _invoice(org_id, "INV-NOREG-1", registered=False)
        sweep = _sweep()
        for _ in range(4):
            await sweep.tick(now=datetime.now(UTC) + LATER)
            await _publish_all(admin_session, org_id)
            await _deliver_all(org_id)
            await _age(admin_session, org_id)
        reclaims = [r for r in await _requests(org_id) if r.payload["trigger"] == "reclaim"]
        assert reclaims == []
        assert await _filings(session, org_id, invoice.id) == []


class TestAnUnansweredInvoiceIsBounded:
    async def test_it_is_requeued_at_rising_attempts_then_ends_terminal(
        self, session, admin_session, org_id, monkeypatch
    ) -> None:
        """An adapter raising outside the three mapped exceptions leaves no row every time. The
        sweep re-queues at attempt n+1 (not always 1), MAX_FILING_ATTEMPTS times, then writes a
        terminal `retry_exhausted` row — which is also what takes it out of discovery."""
        import app.consumers as consumers_module

        class _Broken:
            name = "broken-irp"

            def register(self, payload: Any) -> Any:
                raise RuntimeError("adapter bug")

            def cancel(self, **kwargs: Any) -> Any:  # pragma: no cover
                raise RuntimeError("adapter bug")

        monkeypatch.setattr(consumers_module, "get_irp_client", lambda: _Broken())
        invoice = await _invoice(org_id, "INV-POISON-1", registered=True)
        await _lose_finalize_request(admin_session, org_id)

        sweep = _sweep()
        for _ in range(domain.MAX_FILING_ATTEMPTS + 3):
            await sweep.tick(now=datetime.now(UTC) + LATER)
            await _publish_all(admin_session, org_id)
            await _deliver_all(org_id)
            await _age(admin_session, org_id)

        reclaims = [r for r in await _requests(org_id) if r.payload["trigger"] == "reclaim"]
        assert [r.payload["attempt"] for r in reclaims] == list(
            range(1, domain.MAX_FILING_ATTEMPTS + 1)
        )
        async with session.begin():
            filings = await selectors.filings_for_invoice(session, org_id, invoice.id)
        assert [f.status for f in filings] == ["failed"]
        assert filings[0].error_code == domain.ERR_RETRY_EXHAUSTED
        failed = await _events(session, org_id, services.EVT_INVOICE_FILING_FAILED)
        assert failed[-1].payload["terminal"] is True


class TestARelayOutageIsNotAnAttempt:
    """With the relay down nothing is published: a request that is only queued is not an attempt
    the invoice failed, and re-queueing behind it would only queue a duplicate. Before, every
    queued request counted, and an outage of ~47 minutes ended invoices `retry_exhausted` with a
    "file manually" notice for a filing nobody had ever been asked to make."""

    async def test_a_queued_finalize_request_is_never_requeued_or_exhausted(
        self, session, admin_session, org_id
    ) -> None:
        invoice = await _invoice(org_id, "INV-RELAY-1", registered=True)
        sweep = _sweep()
        for _ in range(domain.MAX_FILING_ATTEMPTS + 3):
            await sweep.tick(now=datetime.now(UTC) + LATER)
            await _age(admin_session, org_id)
        requests = await _requests(org_id)
        assert [r.payload["trigger"] for r in requests] == ["finalized"]
        assert await _filings(session, org_id, invoice.id) == []

    async def test_one_reclaim_waits_behind_the_outage_and_the_invoice_is_not_exhausted(
        self, session, admin_session, org_id
    ) -> None:
        invoice = await _invoice(org_id, "INV-RELAY-2", registered=True)
        await _lose_finalize_request(admin_session, org_id)
        sweep = _sweep()
        for _ in range(domain.MAX_FILING_ATTEMPTS + 3):
            await sweep.tick(now=datetime.now(UTC) + LATER)
            await _age(admin_session, org_id)
        reclaims = [r for r in await _requests(org_id) if r.payload["trigger"] == "reclaim"]
        assert len(reclaims) == 1
        assert await _filings(session, org_id, invoice.id) == []

        # The relay comes back: the queued request is published and, unanswered, is attempt 1.
        await _publish_all(admin_session, org_id)
        await _age(admin_session, org_id)
        await sweep.tick(now=datetime.now(UTC) + LATER)
        reclaims = [r for r in await _requests(org_id) if r.payload["trigger"] == "reclaim"]
        assert [r.payload["attempt"] for r in reclaims] == [1, 2]


class TestDiscoveryIsNotHeadOfLineBlocked:
    async def test_unregistered_invoices_do_not_hide_another_orgs_lost_one(
        self, admin_session, org_id
    ) -> None:
        for number in ("INV-HOL-1", "INV-HOL-2"):
            await _invoice(org_id, number, registered=False)
        org_b = uuid.uuid4()
        await _invoice(org_b, "INV-LOST-1", registered=True)
        await _lose_finalize_request(admin_session, org_b)

        await _sweep(batch_size=2).tick(now=datetime.now(UTC) + LATER)
        assert len(await _requests(org_b)) == 1

    async def test_backing_off_invoices_do_not_hide_another_orgs_lost_one(
        self, admin_session, org_id
    ) -> None:
        """Two lost invoices in org A are re-queued first (they are older) and are now backing
        off; the next tick, same batch size, must reach org B's instead of re-selecting them."""
        for number in ("INV-HOL-3", "INV-HOL-4"):
            await _invoice(org_id, number, registered=True)
        await _lose_finalize_request(admin_session, org_id)
        org_b = uuid.uuid4()
        await _invoice(org_b, "INV-LOST-2", registered=True)
        await _lose_finalize_request(admin_session, org_b)

        sweep = _sweep(batch_size=2)
        await sweep.tick(now=datetime.now(UTC) + LATER)
        assert len(await _requests(org_id)) == 2
        assert await _requests(org_b) == []
        await sweep.tick(now=datetime.now(UTC) + LATER)
        assert len(await _requests(org_b)) == 1
        assert len(await _requests(org_id)) == 2


class TestTwoRegistrationDriversRecordOne:
    async def test_the_second_recorder_returns_the_first_registration(
        self, session, org_id
    ) -> None:
        ctx = admin_ctx(org_id)
        invoice = await _consignment(session, ctx, "INV-RACE-1")
        payload = FileInvoiceIn(organization_id=org_id, invoice_id=invoice.id)
        async with session_scope(organization_id=org_id) as s:
            manual = await services.claim_manual_filing(s, ctx, payload)
        async with session_scope(organization_id=org_id) as s:
            auto = await services.claim_autofile_attempt(s, ctx, invoice.id, attempt=1)
        assert auto.outcome is None and auto.irp_payload is not None
        first = services.call_irp_register(StubIrpClient(), manual.irp_payload)
        second = services.call_irp_register(StubIrpClient(), auto.irp_payload)

        async with session_scope(organization_id=org_id) as s:
            filed = await services.record_manual_filing(
                s, ctx, "stub", invoice.id, first, at=manual.at
            )
        async with session_scope(organization_id=org_id) as s:
            outcome = await services.record_autofile_result(
                s,
                ctx,
                invoice.id,
                second,
                attempt=1,
                retryable=True,
                at=auto.at,
                client_name="stub",
            )
        assert outcome.outcome == "already_filed"
        assert outcome.filing is not None and outcome.filing.id == filed.id
        assert await _filings(session, org_id, invoice.id) == ["filed"]

    async def test_a_late_outage_does_not_land_on_a_filed_row(self, session, org_id) -> None:
        ctx = admin_ctx(org_id)
        invoice = await _consignment(session, ctx, "INV-RACE-2")
        async with session_scope(organization_id=org_id) as s:
            auto = await services.claim_autofile_attempt(s, ctx, invoice.id, attempt=1)
        async with session_scope(organization_id=org_id) as s:
            await services.file_invoice(
                s,
                ctx,
                StubIrpClient(),
                FileInvoiceIn(organization_id=org_id, invoice_id=invoice.id),
            )
        outage = services._RegisterCallOutcome(kind="unavailable", error="timed out")
        async with session_scope(organization_id=org_id) as s:
            outcome = await services.record_manual_filing(
                s, ctx, "stub", invoice.id, outage, at=auto.at
            )
        assert outcome.status == "filed"
        assert await _filings(session, org_id, invoice.id) == ["filed"]


class _CountingEway(StubEwayClient):
    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    def generate(self, payload: EwayPayload) -> Any:
        self.calls += 1
        return super().generate(payload)

    def cancel(self, **kwargs: Any) -> Any:
        self.calls += 1
        return super().cancel(**kwargs)


class _DownEway:
    name = "down-eway"

    def generate(self, payload: EwayPayload) -> Any:
        raise EwayUnavailable("connection reset")

    def cancel(self, **kwargs: Any) -> Any:
        raise EwayUnavailable("connection reset")


class TestAgeAndWindowCaps:
    async def test_an_eway_generation_older_than_a_day_is_not_redriven(
        self, session, org_id
    ) -> None:
        ctx = admin_ctx(org_id)
        invoice = await _consignment(session, ctx, "INV-AGE-1")
        async with session.begin():
            await services.generate_eway_bill(
                session, ctx, _DownEway(), _generate_in(org_id, invoice.id)
            )
        portal = _CountingEway()
        await _sweep(eway_client_factory=lambda: portal).tick(
            now=datetime.now(UTC) + domain.EWAY_REDRIVE_MAX_AGE + timedelta(hours=1)
        )
        assert portal.calls == 0
        bills = await _bills(session, org_id, invoice.id)
        assert bills[0].status == "failed"
        assert bills[0].error_code == domain.ERR_STALE

    async def test_an_irn_withdrawal_past_the_window_is_not_redriven(self, session, org_id) -> None:
        ctx = admin_ctx(org_id)
        invoice = await _filed(session, ctx, org_id, "INV-WIN-1")
        async with session.begin():
            await services.claim_irn_cancellation(
                session,
                ctx,
                CancelFilingIn(organization_id=org_id, invoice_id=invoice.id, reason_code="2"),
            )
        calls: list[str] = []

        class _Spy(StubIrpClient):
            def cancel(self, **kwargs: Any) -> Any:
                calls.append(kwargs["irn"])
                return super().cancel(**kwargs)

        later = datetime.now(UTC) + timedelta(hours=25)
        await _sweep(irp_client_factory=lambda: _Spy()).tick(now=later)
        assert calls == []
        async with session.begin():
            filings = await selectors.filings_for_invoice(session, org_id, invoice.id)
            latest, counts = await selectors.latest_filings(session, org_id, [invoice.id])
        assert filings[0].status == "failed"
        assert filings[0].error_code == domain.ERR_WINDOW_CLOSED
        # The IRN still stands, so the read model must not say "nothing to do".
        state = selectors.to_einvoice_state(
            invoice, latest[invoice.id], counts[invoice.id], at=later
        )
        assert state.terminal is False
        assert state.remedy == "contact_support"
        assert state.ack_date is not None

    async def test_an_eway_withdrawal_past_the_window_is_not_redriven(
        self, session, org_id
    ) -> None:
        ctx = admin_ctx(org_id)
        invoice = await _consignment(session, ctx, "INV-WIN-2")
        async with session.begin():
            await services.generate_eway_bill(
                session, ctx, StubEwayClient(), _generate_in(org_id, invoice.id, distance_km=3000)
            )
        async with session.begin():
            await services.claim_eway_cancellation(
                session,
                ctx,
                CancelEwayIn(organization_id=org_id, invoice_id=invoice.id, reason_code="2"),
            )
        portal = _CountingEway()
        await _sweep(eway_client_factory=lambda: portal).tick(
            now=datetime.now(UTC) + timedelta(hours=25)
        )
        assert portal.calls == 0
        bills = await _bills(session, org_id, invoice.id)
        assert bills[0].status == "failed"
        assert bills[0].error_code == domain.ERR_WINDOW_CLOSED
        assert bills[0].valid_from is not None  # the bill still stands


class TestReadModelAndOrdering:
    async def test_a_refused_withdrawal_leaves_a_remedy_while_the_irn_stands(
        self, session, org_id
    ) -> None:
        ctx = admin_ctx(org_id)
        invoice = await _filed(session, ctx, org_id, "INV-RM-1")

        class _Refusing(StubIrpClient):
            def cancel(self, **kwargs: Any) -> Any:
                from app.irp.base import IrpRejected

                raise IrpRejected("9999", "try again later")

        async with session.begin():
            await services.cancel_filing(
                session,
                ctx,
                _Refusing(),
                CancelFilingIn(organization_id=org_id, invoice_id=invoice.id, reason_code="2"),
            )
            latest, counts = await selectors.latest_filings(session, org_id, [invoice.id])
        state = selectors.to_einvoice_state(invoice, latest[invoice.id], counts[invoice.id])
        assert state.status == "failed"
        assert state.terminal is False
        assert state.remedy == "cancel"
        assert state.cancellable_until is not None

    async def test_a_late_withdrawal_is_not_sorted_behind_the_sweeps_failure(
        self, session, org_id
    ) -> None:
        """The request's portal call outlived the sweep, which gave up (window closed) and wrote
        `failed`; then the request's `cancelled` arrives. It is stamped no earlier than the newest
        row, so it — the true, later fact — is what the read model reports."""
        ctx = admin_ctx(org_id)
        invoice = await _filed(session, ctx, org_id, "INV-ORD-1")
        async with session.begin():
            claimed = await services.claim_irn_cancellation(
                session,
                ctx,
                CancelFilingIn(organization_id=org_id, invoice_id=invoice.id, reason_code="2"),
            )
        outcome = services.call_irp_cancel(StubIrpClient(), claimed.facts)
        await _sweep().tick(now=datetime.now(UTC) + timedelta(hours=25))
        assert (await _filings(session, org_id, invoice.id))[0] == "failed"

        async with session_scope(organization_id=org_id) as s:
            await services.record_irn_cancellation(
                s, ctx, claimed.facts, outcome, at=claimed.facts.at
            )
        assert (await _filings(session, org_id, invoice.id))[0] == "cancelled"


class TestTheStubEwayPortalAnswersRepeatsForReal:
    async def test_a_resumed_generation_is_answered_already_generated(
        self, session, org_id
    ) -> None:
        """No fake: the real stub numbers a bill from its idempotency key and answers a repeat of
        the key with `EwayAlreadyGenerated` — so a crash-then-resume records the one bill."""
        ctx = admin_ctx(org_id)
        invoice = await _consignment(session, ctx, "INV-STUB-1")
        portal = StubEwayClient()
        async with session.begin():
            facts = await services.claim_eway_generation(
                session, ctx, _generate_in(org_id, invoice.id)
            )
        crashed = services.call_eway_generate(portal, facts.payload)
        assert crashed.ack is not None

        again = services.call_eway_generate(portal, facts.payload)
        assert again.kind == "generated"
        assert again.ack == crashed.ack  # via EwayAlreadyGenerated, not a second bill

        await _sweep(eway_client_factory=lambda: portal).tick(now=datetime.now(UTC) + LATER)
        bills = await _bills(session, org_id, invoice.id)
        assert bills[0].status == "generated"
        assert bills[0].eway_bill_no == crashed.ack.eway_bill_no
        # A fresh process (a restart after the crash) numbers the same key the same way.
        assert StubEwayClient().generate(facts.payload).eway_bill_no == crashed.ack.eway_bill_no


class TestThePortalCallIsOffTheLoop:
    async def test_a_hanging_portal_is_an_outage_not_a_blocked_request(
        self, client, session, org_id, monkeypatch
    ) -> None:
        """The call runs on a worker thread and is bounded by `portal_call_timeout_seconds`: a
        portal that hangs yields the `cancelling` claim (the withdrawal is owed) instead of a
        request — and an event loop — stuck behind it."""
        import time

        from app.config import settings
        from app.deps import get_irp_client
        from app.main import app
        from tests.helpers import service_token_headers

        invoice = await _filed(session, admin_ctx(org_id), org_id, "INV-HANG-1")
        invoice_id = invoice.id
        monkeypatch.setattr(settings(), "portal_call_timeout_seconds", 0.2)

        class _Hanging(StubIrpClient):
            def cancel(self, **kwargs: Any) -> Any:
                time.sleep(3.0)
                return super().cancel(**kwargs)

        app.dependency_overrides[get_irp_client] = lambda: _Hanging()
        try:
            started = datetime.now(UTC)
            res = await client.post(
                "/internal/v1/einvoice/filings:cancel",
                headers=service_token_headers(),
                json={
                    "organization_id": str(org_id),
                    "invoice_id": str(invoice_id),
                    "reason_code": "2",
                },
            )
            elapsed = datetime.now(UTC) - started
        finally:
            app.dependency_overrides.pop(get_irp_client, None)
        assert res.status_code == 201, res.text
        assert res.json()["status"] == "cancelling"
        assert elapsed < timedelta(seconds=2.5)


class TestTheSweepReadsTheClockPerInvoice:
    async def test_each_invoice_is_judged_against_its_own_now(self, monkeypatch) -> None:
        import app.cron as cron_module

        ticks = iter(datetime(2026, 9, 1, tzinfo=UTC) + timedelta(minutes=i) for i in range(10))

        class _Clock(datetime):
            @classmethod
            def now(cls, tz: Any = None) -> datetime:  # type: ignore[override]
                return next(ticks)

        seen: list[datetime] = []

        async def _lost(session: Any, **kwargs: Any) -> list[tuple[uuid.UUID, uuid.UUID]]:
            return [(uuid.uuid4(), uuid.uuid4()), (uuid.uuid4(), uuid.uuid4())]

        async def _none(session: Any, **kwargs: Any) -> list[tuple[uuid.UUID, uuid.UUID]]:
            return []

        async def _record(self: Any, org: Any, inv: Any, now: datetime, report: Any) -> None:
            seen.append(now)

        monkeypatch.setattr(cron_module, "datetime", _Clock)
        monkeypatch.setattr(cron_module.selectors, "due_filing_reclaims", _lost)
        monkeypatch.setattr(cron_module.selectors, "due_stale_filings", _none)
        monkeypatch.setattr(cron_module.selectors, "due_stale_eway", _none)
        monkeypatch.setattr(cron_module.FilingReclaimSweep, "_resume_one", _record)

        await _sweep().tick()
        assert len(seen) == 2 and seen[0] < seen[1]


def test_the_discovery_event_name_matches_the_service_constant() -> None:
    assert selectors.FILING_REQUESTED_EVENT == services.EVT_INVOICE_FILING_REQUESTED


class TestThePortalTimeoutFitsTheReclaimWindow:
    def test_a_portal_call_that_could_outlive_its_claim_is_refused(self) -> None:
        """A claim is re-drivable `FILING_RECLAIM_AFTER` after it was made; a portal call allowed
        to run longer than half of that (less a minute) could still be answering when the sweep
        re-drives the same claim."""
        from pydantic import ValidationError

        from app.config import Settings

        with pytest.raises(ValidationError, match="portal_call_timeout_seconds"):
            Settings(portal_call_timeout_seconds=150)
        assert Settings(portal_call_timeout_seconds=120).portal_call_timeout_seconds == 120

    def test_the_default_fits(self) -> None:
        from app.config import settings

        cfg = settings()
        assert (
            2 * cfg.portal_call_timeout_seconds + 60 <= domain.FILING_RECLAIM_AFTER.total_seconds()
        )
