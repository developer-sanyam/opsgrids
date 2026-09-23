"""Auto-filing: the queue, the guard, and the two ways it is allowed to stop.

Filing an e-invoice is no longer something a person does. Finalizing a reportable document stages
a request on the outbox, the consumer picks it up and files it, and the only remaining questions
are the ones that cost money when they are answered wrongly:

* is the request **staged and not sent** — i.e. does the attempt wait for the invoice to commit;
* is a document already carrying an IRN **never sent again**, even under redelivery, a crossing
  manual filing, or a second finalize;
* does a portal **rejection** stop at once, and does an **outage** retry — bounded, and then stop
  with something a person can see.

These drive the real consumer against real PostgreSQL, with the portal replaced per test by a
double that counts its calls. Counting is the point: "did not file twice" is a claim about a
network call, and asserting on rows would let a second call through as long as it happened to
produce the same row.
"""

from __future__ import annotations

import itertools
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import func, select

from app import domain, selectors, services
from app.config import settings
from app.consumers import file_requested_invoice
from app.irp.base import IrpAck, IrpInvoicePayload, IrpUnavailable
from app.irp.stub import StubIrpClient
from app.models import EInvoiceFiling, OutboxEvent, ProcessedEvent
from app.schemas import (
    CustomerCreate,
    FileInvoiceIn,
    GstRegistrationUpsert,
    InvoiceCreate,
    InvoiceLineCreate,
)
from platform_core.db import dispose_engine, init_engine
from platform_core.events import PLATFORM_SCHEDULE_REQUESTED
from tests.helpers import admin_ctx

pytestmark = pytest.mark.integration

SELLER = "27AABCA9012D1Z5"
BUYER_SAME_STATE = "27AAECO9012H1ZG"


@pytest.fixture(autouse=True)
async def _engine() -> Any:
    """The consumer opens its own `session_scope`, which needs the process engine initialised —
    the app lifespan does this in production; here the fixture stands in for it."""
    init_engine(settings())
    yield
    await dispose_engine()


# --------------------------------------------------------------------- portal doubles


class CountingPortal:
    """A portal that records every call. `calls` is what "never filed twice" is asserted on."""

    name = "counting-irp"

    def __init__(self) -> None:
        self.calls: list[IrpInvoicePayload] = []
        self._real = StubIrpClient()

    def register(self, payload: IrpInvoicePayload) -> IrpAck:
        self.calls.append(payload)
        return self._real.register(payload)

    def cancel(self, **kwargs: Any) -> Any:
        raise NotImplementedError


class DownPortal:
    """A portal that is reachable by nobody. Every call is an outage, and they are counted."""

    name = "down-irp"

    def __init__(self) -> None:
        self.calls = 0

    def register(self, payload: IrpInvoicePayload) -> IrpAck:
        self.calls += 1
        raise IrpUnavailable("the portal did not answer in time")

    def cancel(self, **kwargs: Any) -> Any:
        raise IrpUnavailable("the portal did not answer in time")


# --------------------------------------------------------------------- fixtures


async def _register(session, ctx, gstin: str = SELLER) -> None:
    await services.set_gst_registration(
        session,
        ctx,
        GstRegistrationUpsert(gstin=gstin, legal_name="OpsGrids Industries Private Limited"),
    )


async def _customer(session, ctx, *, gstin: str | None = BUYER_SAME_STATE, name: str = "Buyer"):
    return await services.create_customer(
        session,
        ctx,
        CustomerCreate(
            name=name,
            contact_name="Contact",
            email="ap@example.in",
            gstin=gstin,
            city="Pune",
            state="Maharashtra",
        ),
    )


def _invoice_payload(customer_id, number: str, **overrides) -> InvoiceCreate:
    payload: dict[str, Any] = {
        "number": number,
        "customer_id": customer_id,
        "issued_date": datetime(2026, 7, 1, tzinfo=UTC).date(),
        "due_date": datetime(2026, 7, 31, tzinfo=UTC).date(),
        "tax_rate_pct": 18,
        "lines": [
            InvoiceLineCreate(
                description="Cotton yarn",
                quantity=100,
                unit_price_minor=25_000,
                hsn_sac="520512",
            )
        ],
    }
    payload.update(overrides)
    return InvoiceCreate(**payload)


async def _finalize(session, ctx, number: str, **overrides):
    """Register the seller, create a buyer and finalize one reportable invoice."""
    async with session.begin():
        await _register(session, ctx)
        customer = await _customer(session, ctx)
        return await services.create_invoice(
            session, ctx, _invoice_payload(customer.id, number, **overrides)
        )


async def _requests(session, org_id) -> list[OutboxEvent]:
    stmt = (
        select(OutboxEvent)
        .where(
            OutboxEvent.organization_id == org_id,
            OutboxEvent.event_type == services.EVT_INVOICE_FILING_REQUESTED,
        )
        .order_by(OutboxEvent.created_at)
    )
    return list((await session.execute(stmt)).scalars().all())


def _envelope(row: OutboxEvent) -> dict[str, Any]:
    """The envelope the relay would have built for one staged outbox row."""
    return {
        "event_id": str(row.id),
        "event_type": row.event_type,
        "organization_id": str(row.organization_id),
        "payload": row.payload,
    }


async def _scheduled_requests(session, org_id) -> list[OutboxEvent]:
    """Rows finance staged asking the scheduler to deliver a filing retry later (ADR-0015) —
    `request_filing`'s other branch, taken whenever `not_before` is still in the future."""
    stmt = (
        select(OutboxEvent)
        .where(
            OutboxEvent.organization_id == org_id,
            OutboxEvent.event_type == PLATFORM_SCHEDULE_REQUESTED,
        )
        .order_by(OutboxEvent.created_at)
    )
    return list((await session.execute(stmt)).scalars().all())


def _fire(row: OutboxEvent) -> dict[str, Any]:
    """The envelope the scheduler would eventually publish once one of its own rows' `due_at`
    arrives — its own outbox row, addressed at the target event type, is what the relay turns
    into a message; this stands in for that the same way `_envelope` stands in for the relay
    itself. A fresh `event_id`, because that is what a brand-new outbox row on the scheduler's
    own outbox actually gets."""
    target = row.payload["target"]
    return {
        "event_id": str(uuid.uuid4()),
        "event_type": target["event_type"],
        "organization_id": str(row.organization_id),
        "payload": target["payload"],
    }


# --------------------------------------------------------------------- the trigger


class TestFinalizeEnqueues:
    async def test_finalizing_a_reportable_document_stages_a_filing_request(
        self, session, org_id
    ) -> None:
        """Staged, not sent. The row commits with the invoice, and the relay carries it onward —
        so there is no window in which the platform is filing a document that does not exist yet,
        and none in which a rolled-back invoice left a filing request behind."""
        ctx = admin_ctx(org_id)
        invoice = await _finalize(session, ctx, "INV-AUTO-1")
        async with session.begin():
            rows = await _requests(session, org_id)
        assert len(rows) == 1
        payload = rows[0].payload
        assert payload["invoice_id"] == str(invoice.id)
        assert payload["attempt"] == 1
        assert payload["trigger"] == "finalized"
        assert payload["max_attempts"] == domain.MAX_FILING_ATTEMPTS
        assert rows[0].aggregate_type == "invoice"

    async def test_finalizing_files_nothing_inline(self, session, org_id) -> None:
        """The whole reason this is an event. A filing attempt inside the request that raised the
        invoice would tie a customer's ability to bill to a government portal's uptime."""
        ctx = admin_ctx(org_id)
        invoice = await _finalize(session, ctx, "INV-AUTO-2")
        async with session.begin():
            filings = await selectors.filings_for_invoice(session, org_id, invoice.id)
        assert filings == []

    async def test_a_b2c_document_stages_no_request(self, session, org_id) -> None:
        """B2C is not reportable, so there is nothing to queue. A request for it would be work the
        consumer only does to discover it should not have."""
        ctx = admin_ctx(org_id)
        async with session.begin():
            await _register(session, ctx)
            customer = await _customer(session, ctx, gstin=None, name="Walk-in")
            await services.create_invoice(
                session, ctx, _invoice_payload(customer.id, "INV-AUTO-B2C")
            )
        async with session.begin():
            assert await _requests(session, org_id) == []

    async def test_a_draft_or_void_document_stages_no_request(self, session, org_id) -> None:
        """Neither is a supply anybody has been told about, and reporting one is not a harmless
        extra: an IRN is a hash of the document number and the financial year, so a draft filed by
        mistake burns the number the real document will need."""
        ctx = admin_ctx(org_id)
        async with session.begin():
            await _register(session, ctx)
            customer = await _customer(session, ctx)
            await services.create_invoice(
                session, ctx, _invoice_payload(customer.id, "INV-AUTO-DRAFT", status="draft")
            )
            await services.create_invoice(
                session, ctx, _invoice_payload(customer.id, "INV-AUTO-VOID", status="void")
            )
        async with session.begin():
            assert await _requests(session, org_id) == []

    async def test_a_document_voided_before_the_consumer_runs_is_not_filed(
        self, session, org_id
    ) -> None:
        """The lifecycle is checked again when the request is handled, not only when it was
        queued: a stale message must not file a document that is no longer reportable."""
        ctx = admin_ctx(org_id)
        async with session.begin():
            await _register(session, ctx)
            customer = await _customer(session, ctx)
            voided = await services.create_invoice(
                session, ctx, _invoice_payload(customer.id, "INV-AUTO-VOID-2", status="void")
            )
            portal = CountingPortal()
            result = await services.autofile_invoice(session, ctx, portal, invoice_id=voided.id)
        assert result.outcome == "not_required"
        assert portal.calls == []

    async def test_the_lifecycle_guard_is_stricter_than_the_statutory_one(self) -> None:
        """`requires_einvoice` answers "is this kind of supply reportable"; `should_auto_file` adds
        "and has it actually been issued". The manual route is deliberately not held to the
        second — somebody naming one document is making a decision, the automation is not."""
        assert domain.requires_einvoice(supply_type="b2b", document_type="invoice") is True
        for status in ("sent", "paid", "overdue"):
            assert domain.should_auto_file(
                supply_type="b2b", document_type="invoice", status=status
            )
        for status in domain.UNREPORTED_STATUSES:
            assert not domain.should_auto_file(
                supply_type="b2b", document_type="invoice", status=status
            )
        assert not domain.should_auto_file(
            supply_type="b2c", document_type="invoice", status="sent"
        )

    async def test_the_whole_demo_book_queues_exactly_its_reportable_documents(
        self, session, org_id, book
    ) -> None:
        """The seed drives the real write path, so this is the rule applied to twenty-one real
        documents at once — both split shapes, a B2C sale, a credit note, a draft and a void.
        Compared against the predicate rather than a hard-coded count, so it stays true when the
        demo book changes."""
        async with session.begin():
            invoices = await selectors.all_invoices(session, org_id)
            queued = {row.payload["invoice_id"] for row in await _requests(session, org_id)}
        expected = {
            str(inv.id)
            for inv in invoices
            if domain.should_auto_file(
                supply_type=inv.supply_type,
                document_type=inv.document_type,
                status=inv.status,
            )
        }
        assert queued == expected
        assert queued, "the demo book raised nothing reportable — the fixture has drifted"
        assert len(queued) < len(invoices), "every document queued; the exclusions did nothing"

    async def test_a_credit_note_is_queued_like_an_invoice(self, session, org_id) -> None:
        """A correction against a reported document is itself reported — GST's own rule, and the
        reason this platform's append-only model maps onto the portal with nothing left over."""
        ctx = admin_ctx(org_id)
        invoice = await _finalize(session, ctx, "INV-AUTO-CN-BASE")
        async with session.begin():
            credit = await services.create_invoice(
                session,
                ctx,
                _invoice_payload(
                    invoice.customer_id,
                    "CN-AUTO-1",
                    document_type="credit_note",
                    corrects_invoice_id=invoice.id,
                    lines=[
                        InvoiceLineCreate(
                            description="Return",
                            quantity=-10,
                            unit_price_minor=25_000,
                            hsn_sac="520512",
                        )
                    ],
                ),
            )
        async with session.begin():
            queued = {row.payload["invoice_id"] for row in await _requests(session, org_id)}
        assert queued == {str(invoice.id), str(credit.id)}


# --------------------------------------------------------------------- the consumer


class TestTheConsumerFiles:
    async def test_the_queued_request_is_filed_and_the_irn_is_persisted(
        self, session, org_id, monkeypatch
    ) -> None:
        """End to end through the real handler: the attempt row carries the IRN, the
        acknowledgement number and date, and the signed QR — verbatim, because the signature is
        over exactly those bytes."""
        ctx = admin_ctx(org_id)
        invoice = await _finalize(session, ctx, "INV-AUTO-3")
        portal = CountingPortal()
        monkeypatch.setattr("app.consumers.get_irp_client", lambda: portal)

        async with session.begin():
            rows = await _requests(session, org_id)
        await file_requested_invoice(_envelope(rows[0]))

        async with session.begin():
            filings = await selectors.filings_for_invoice(session, org_id, invoice.id)
        assert len(filings) == 1
        filed = filings[0]
        assert filed.status == "filed"
        assert filed.irn and len(filed.irn) == 64
        assert filed.ack_no and filed.ack_date is not None
        assert filed.qr_payload and filed.qr_payload.count(".") == 2
        assert len(portal.calls) == 1

    async def test_the_read_model_shows_the_document_as_registered(
        self, client, session, org_id, monkeypatch
    ) -> None:
        ctx = admin_ctx(org_id)
        invoice = await _finalize(session, ctx, "INV-AUTO-4")
        monkeypatch.setattr("app.consumers.get_irp_client", CountingPortal)

        async with session.begin():
            rows = await _requests(session, org_id)
        await file_requested_invoice(_envelope(rows[0]))

        state = (await client.get(f"/api/v1/finance/invoices/{invoice.id}")).json()["einvoice"]
        assert state["status"] == "filed"
        assert state["attempts"] == 1
        assert state["terminal"] is False
        assert state["remedy"] == "cancel"

    async def test_the_success_publishes_the_filed_event(
        self, session, org_id, monkeypatch
    ) -> None:
        ctx = admin_ctx(org_id)
        await _finalize(session, ctx, "INV-AUTO-5")
        monkeypatch.setattr("app.consumers.get_irp_client", CountingPortal)

        async with session.begin():
            rows = await _requests(session, org_id)
        await file_requested_invoice(_envelope(rows[0]))

        async with session.begin():
            filed = (
                (
                    await session.execute(
                        select(OutboxEvent).where(
                            OutboxEvent.organization_id == org_id,
                            OutboxEvent.event_type == services.EVT_INVOICE_FILED,
                        )
                    )
                )
                .scalars()
                .all()
            )
        assert len(filed) == 1
        assert filed[0].payload["trigger"] == "auto"
        assert filed[0].payload["attempt"] == 1
        assert filed[0].payload["irn"]


# --------------------------------------------------------------------- idempotency


class TestFilingIsIdempotent:
    async def test_a_redelivered_request_does_not_reach_the_portal(
        self, session, org_id, monkeypatch
    ) -> None:
        """Delivery is at-least-once. The same event twice must cost exactly one call and leave
        exactly one row — proven on the *call count*, because asserting on rows alone would let a
        second registration through as long as it produced the same row."""
        ctx = admin_ctx(org_id)
        invoice = await _finalize(session, ctx, "INV-AUTO-DUP-1")
        portal = CountingPortal()
        monkeypatch.setattr("app.consumers.get_irp_client", lambda: portal)

        async with session.begin():
            rows = await _requests(session, org_id)
        envelope = _envelope(rows[0])
        await file_requested_invoice(envelope)
        await file_requested_invoice(envelope)

        assert len(portal.calls) == 1
        async with session.begin():
            filings = await selectors.filings_for_invoice(session, org_id, invoice.id)
        assert len(filings) == 1

    async def test_a_distinct_request_for_a_document_already_filed_never_calls_the_portal(
        self, session, org_id, monkeypatch
    ) -> None:
        """The guard that matters. Event-id idempotency does not cover this: a retry that crossed
        with a manual filing, or a second finalize event, is a *different* event naming the same
        document. `live_registration` is consulted before the client is touched, so the portal is
        never asked to register something it has already registered — which it answers with
        `2150 Duplicate IRN`, and discovering a fact you already had by being refused is how an
        integration files every invoice twice under load."""
        ctx = admin_ctx(org_id)
        invoice = await _finalize(session, ctx, "INV-AUTO-DUP-2")
        portal = CountingPortal()
        monkeypatch.setattr("app.consumers.get_irp_client", lambda: portal)

        async with session.begin():
            rows = await _requests(session, org_id)
        await file_requested_invoice(_envelope(rows[0]))
        assert len(portal.calls) == 1

        # A second, genuinely distinct request for the same document — a fresh event id.
        second = _envelope(rows[0])
        second["event_id"] = str(uuid.uuid4())
        await file_requested_invoice(second)

        assert len(portal.calls) == 1, "a document already carrying an IRN was filed again"
        async with session.begin():
            filings = await selectors.filings_for_invoice(session, org_id, invoice.id)
        assert [f.status for f in filings] == ["filed"]

    async def test_the_guard_holds_against_a_manual_filing(
        self, session, org_id, monkeypatch
    ) -> None:
        """The realistic race: an operator files through the internal route while the consumer's
        message is still in flight. The queued attempt must find the registration and stop."""
        ctx = admin_ctx(org_id)
        invoice = await _finalize(session, ctx, "INV-AUTO-DUP-3")
        async with session.begin():
            await services.file_invoice(
                session,
                ctx,
                StubIrpClient(),
                FileInvoiceIn(organization_id=org_id, invoice_id=invoice.id),
            )

        portal = CountingPortal()
        monkeypatch.setattr("app.consumers.get_irp_client", lambda: portal)
        async with session.begin():
            rows = await _requests(session, org_id)
        await file_requested_invoice(_envelope(rows[0]))

        assert portal.calls == []
        async with session.begin():
            filings = await selectors.filings_for_invoice(session, org_id, invoice.id)
        assert [f.status for f in filings] == ["filed"]

    async def test_the_event_is_marked_processed(self, session, org_id, monkeypatch) -> None:
        """The idempotency ledger is written in the same transaction as the side effects, so a
        crash between the two cannot leave an event handled but unrecorded."""
        ctx = admin_ctx(org_id)
        await _finalize(session, ctx, "INV-AUTO-DUP-4")
        monkeypatch.setattr("app.consumers.get_irp_client", CountingPortal)

        async with session.begin():
            rows = await _requests(session, org_id)
        await file_requested_invoice(_envelope(rows[0]))

        async with session.begin():
            marked = await session.get(ProcessedEvent, rows[0].id)
        assert marked is not None
        assert marked.event_type == services.EVT_INVOICE_FILING_REQUESTED


# --------------------------------------------------------------------- retries and giving up


class TestRetries:
    async def test_an_outage_records_a_pending_attempt_and_schedules_the_next(
        self, session, org_id, monkeypatch
    ) -> None:
        """The document is fine, the portal is not — so the attempt is `pending`, and the retry is
        **scheduled** rather than redelivered at once (ADR-0015): its backoff is still in the
        future, so `request_filing` asks the scheduler to hold it instead of queuing another
        `filing_requested` directly."""
        ctx = admin_ctx(org_id)
        invoice = await _finalize(session, ctx, "INV-AUTO-RETRY-1")
        portal = DownPortal()
        monkeypatch.setattr("app.consumers.get_irp_client", lambda: portal)

        async with session.begin():
            rows = await _requests(session, org_id)
        await file_requested_invoice(_envelope(rows[0]))

        async with session.begin():
            filings = await selectors.filings_for_invoice(session, org_id, invoice.id)
            queued = await _requests(session, org_id)
            scheduled = await _scheduled_requests(session, org_id)
        assert [f.status for f in filings] == ["pending"]
        assert filings[0].error_code == domain.ERR_PORTAL_UNAVAILABLE
        # Only the finalize request was published directly; the retry went to the scheduler.
        assert len(queued) == 1
        assert len(scheduled) == 1
        retry = scheduled[0].payload["target"]["payload"]
        assert retry["attempt"] == 2
        assert retry["trigger"] == "retry"
        assert retry["not_before"] is not None
        assert scheduled[0].payload["due_at"] == retry["not_before"]

    async def test_the_backoff_grows_and_is_capped(self) -> None:
        """Exponential so a struggling portal is not hammered, capped because the obligation has a
        deadline — a backoff that grows past the reporting window has stopped being patience."""
        delays = [domain.filing_retry_delay(n) for n in range(1, 9)]
        assert delays[0] == domain.FILING_RETRY_BASE
        assert delays[1] == domain.FILING_RETRY_BASE * 2
        assert all(b >= a for a, b in itertools.pairwise(delays))
        assert delays[-1] == domain.FILING_RETRY_CEILING
        with pytest.raises(ValueError, match="1-based"):
            domain.filing_retry_delay(0)

    async def test_the_retry_chain_stops_at_the_budget_with_a_terminal_row(
        self, session, org_id, monkeypatch
    ) -> None:
        """Drain the whole chain against a portal that never answers. Every attempt is recorded,
        the last one is `failed` rather than `pending`, and nothing further is queued — an
        unbounded retry is indistinguishable from an outage nobody noticed."""
        ctx = admin_ctx(org_id)
        invoice = await _finalize(session, ctx, "INV-AUTO-RETRY-2")
        portal = DownPortal()
        monkeypatch.setattr("app.consumers.get_irp_client", lambda: portal)

        # `_fire` delivers a retry the instant it is scheduled, not `FILING_RETRY_CEILING` (up to
        # an hour) later the way the real scheduler would — this loop cannot wait for real clock
        # time to actually reach every retry's `not_before`. The consumer's early-arrival guard
        # (ADR-0015 §5) compares against the real wall clock, so without this the whole chain
        # would look like it kept arriving early and never advance past attempt 1. Frozen safely
        # past the worst-case cumulative backoff this chain can produce.
        frozen_now = datetime.now(UTC) + domain.FILING_RETRY_CEILING * domain.MAX_FILING_ATTEMPTS

        class _FrozenNow(datetime):
            @classmethod
            def now(cls, tz=None):
                return frozen_now

        monkeypatch.setattr("app.consumers.datetime", _FrozenNow)

        # Every retry after the first is scheduled rather than queued directly (ADR-0015), so
        # draining the whole chain means feeding both a direct `_requests` row and, once one
        # exists, the envelope a scheduler firing would eventually publish (`_fire`).
        handled: set[uuid.UUID] = set()
        for _ in range(domain.MAX_FILING_ATTEMPTS + 3):
            async with session.begin():
                all_direct = await _requests(session, org_id)
                all_scheduled = await _scheduled_requests(session, org_id)
                direct = [row for row in all_direct if row.id not in handled]
                scheduled = [row for row in all_scheduled if row.id not in handled]
            pending_envelopes = []
            for row in direct:
                handled.add(row.id)
                pending_envelopes.append(_envelope(row))
            for row in scheduled:
                handled.add(row.id)
                pending_envelopes.append(_fire(row))
            if not pending_envelopes:
                break
            for envelope in pending_envelopes:
                await file_requested_invoice(envelope)

        async with session.begin():
            filings = await selectors.filings_for_invoice(session, org_id, invoice.id)
            queued = await _requests(session, org_id)
            scheduled = await _scheduled_requests(session, org_id)

        assert portal.calls == domain.MAX_FILING_ATTEMPTS
        assert len(filings) == domain.MAX_FILING_ATTEMPTS
        # Newest first: the last attempt gave up, the earlier ones were waiting for it.
        assert filings[0].status == "failed"
        assert filings[0].error_code == domain.ERR_RETRY_EXHAUSTED
        assert {f.status for f in filings[1:]} == {"pending"}
        assert len(queued) + len(scheduled) == domain.MAX_FILING_ATTEMPTS, (
            "a retry was queued or scheduled past the budget"
        )

    async def test_giving_up_surfaces_to_the_operator(self, client, session, org_id) -> None:
        """A terminal failure that nobody can see is the same as no failure at all. The read model
        says `terminal`, and names the platform's own error code rather than a portal one — so a
        support conversation is never ambiguous about who refused what."""
        ctx = admin_ctx(org_id)
        invoice = await _finalize(session, ctx, "INV-AUTO-RETRY-3")
        async with session.begin():
            result = await services.autofile_invoice(
                session,
                ctx,
                DownPortal(),
                invoice_id=invoice.id,
                attempt=domain.MAX_FILING_ATTEMPTS,
            )
        assert result.outcome == "exhausted"

        state = (await client.get(f"/api/v1/finance/invoices/{invoice.id}")).json()["einvoice"]
        assert state["status"] == "failed"
        assert state["terminal"] is True
        assert state["error_code"] == domain.ERR_RETRY_EXHAUSTED
        # Still required, still unfiled: giving up does not make the obligation go away.
        assert state["required"] is True
        assert state["remedy"] is None

    async def test_the_exhausted_event_is_not_retryable(self, session, org_id) -> None:
        ctx = admin_ctx(org_id)
        invoice = await _finalize(session, ctx, "INV-AUTO-RETRY-4")
        async with session.begin():
            await services.autofile_invoice(
                session,
                ctx,
                DownPortal(),
                invoice_id=invoice.id,
                attempt=domain.MAX_FILING_ATTEMPTS,
            )
        async with session.begin():
            failed = (
                (
                    await session.execute(
                        select(OutboxEvent).where(
                            OutboxEvent.organization_id == org_id,
                            OutboxEvent.event_type == services.EVT_INVOICE_FILING_FAILED,
                        )
                    )
                )
                .scalars()
                .all()
            )
            queued = await _requests(session, org_id)
        assert len(failed) == 1
        assert failed[0].payload["retryable"] is False
        assert failed[0].payload["terminal"] is True
        # The finalize request, and nothing after it: the budget was already spent.
        assert len(queued) == 1


# --------------------------------------------------------------------- not_before (ADR-0015)


class TestNotBeforeScheduling:
    async def test_a_past_not_before_is_dispatched_immediately(self, session, org_id) -> None:
        """`not_before` in the past must behave exactly as if it were absent — the platform must
        never wait for a moment that has already happened, and this is the one branch a bare
        `> now` comparison could get backwards."""
        ctx = admin_ctx(org_id)
        invoice = await _finalize(session, ctx, "INV-AUTO-PASTNB-1")

        async with session.begin():
            services.request_filing(
                session,
                ctx,
                invoice,
                attempt=2,
                not_before=datetime.now(UTC) - timedelta(minutes=1),
                trigger="retry",
            )

        async with session.begin():
            direct = await _requests(session, org_id)
            scheduled = await _scheduled_requests(session, org_id)
        # One from finalize (attempt 1), one from the manually staged past-due retry — both
        # published directly, none scheduled.
        assert len(direct) == 2
        assert scheduled == []

    async def test_a_null_not_before_is_dispatched_immediately(self, session, org_id) -> None:
        ctx = admin_ctx(org_id)
        invoice = await _finalize(session, ctx, "INV-AUTO-NONB-1")

        async with session.begin():
            services.request_filing(session, ctx, invoice, attempt=1, trigger="finalized")

        async with session.begin():
            direct = await _requests(session, org_id)
            scheduled = await _scheduled_requests(session, org_id)
        assert len(direct) == 2
        assert scheduled == []

    async def test_early_arrival_is_refused_and_rearmed(self, session, org_id, monkeypatch) -> None:
        """A `filing_requested` message naming a `not_before` still in the future must not be
        acted on even if it reaches the consumer early — defence in depth against a broker or an
        operator redelivering it ahead of schedule (ADR-0015 §5)."""
        ctx = admin_ctx(org_id)
        await _finalize(session, ctx, "INV-AUTO-EARLY-1")
        portal = CountingPortal()
        monkeypatch.setattr("app.consumers.get_irp_client", lambda: portal)

        async with session.begin():
            rows = await _requests(session, org_id)
        finalize_envelope = _envelope(rows[0])
        future = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
        early_event_id = str(uuid.uuid4())
        early_envelope = {
            **finalize_envelope,
            "event_id": early_event_id,
            "payload": {**finalize_envelope["payload"], "attempt": 2, "not_before": future},
        }

        await file_requested_invoice(early_envelope)

        assert portal.calls == []
        async with session.begin():
            filings = await selectors.filings_for_invoice(
                session, org_id, uuid.UUID(finalize_envelope["payload"]["invoice_id"])
            )
            scheduled = await _scheduled_requests(session, org_id)
            processed = await session.get(ProcessedEvent, uuid.UUID(early_event_id))
        # Nothing was ever attempted against the portal for this attempt.
        assert filings == []
        assert len(scheduled) == 1
        assert scheduled[0].payload["due_at"] == future
        assert scheduled[0].payload["target"]["payload"]["attempt"] == 2
        # `early-arrival:{event_id}` in the re-armed key: the original attempt-2 key already
        # belongs to whichever schedule produced this very message.
        assert early_event_id in scheduled[0].payload["idempotency_key"]
        # The early message itself is not redelivered forever.
        assert processed is not None

    async def test_early_arrival_is_idempotent_under_redelivery(
        self, session, org_id, monkeypatch
    ) -> None:
        ctx = admin_ctx(org_id)
        await _finalize(session, ctx, "INV-AUTO-EARLY-2")
        portal = CountingPortal()
        monkeypatch.setattr("app.consumers.get_irp_client", lambda: portal)

        async with session.begin():
            rows = await _requests(session, org_id)
        finalize_envelope = _envelope(rows[0])
        future = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
        early_envelope = {
            **finalize_envelope,
            "event_id": str(uuid.uuid4()),
            "payload": {**finalize_envelope["payload"], "attempt": 2, "not_before": future},
        }

        await file_requested_invoice(early_envelope)
        await file_requested_invoice(early_envelope)  # redelivered

        assert portal.calls == []
        async with session.begin():
            scheduled = await _scheduled_requests(session, org_id)
        assert len(scheduled) == 1

    async def test_a_not_before_within_the_clock_tolerance_is_not_early_arrival(
        self, session, org_id, monkeypatch
    ) -> None:
        """A `not_before` a few seconds in the future is ordinary scheduling latency, not an
        early arrival — the guard forgives `domain.FILING_EARLY_ARRIVAL_TOLERANCE` of clock
        skew between the scheduler's clock and this pod's."""
        ctx = admin_ctx(org_id)
        await _finalize(session, ctx, "INV-AUTO-SKEW-1")
        portal = CountingPortal()
        monkeypatch.setattr("app.consumers.get_irp_client", lambda: portal)

        async with session.begin():
            rows = await _requests(session, org_id)
        finalize_envelope = _envelope(rows[0])
        barely_future = (datetime.now(UTC) + domain.FILING_EARLY_ARRIVAL_TOLERANCE / 2).isoformat()
        envelope = {
            **finalize_envelope,
            "event_id": str(uuid.uuid4()),
            "payload": {**finalize_envelope["payload"], "attempt": 2, "not_before": barely_future},
        }

        await file_requested_invoice(envelope)

        assert len(portal.calls) == 1
        async with session.begin():
            scheduled = await _scheduled_requests(session, org_id)
        assert scheduled == []


class TestEnvelopeOrganizationIsTrustedOverThePayload:
    """The tenant is bound from the envelope's own `organization_id`, never the payload's
    (ADR-0015 review fix): the envelope's is a column the producer's row-level security
    enforced, the payload is JSON along for the ride."""

    async def test_a_payload_naming_a_different_organization_is_refused(
        self, session, org_id, monkeypatch
    ) -> None:
        other_org = uuid.uuid4()
        ctx = admin_ctx(org_id)
        await _finalize(session, ctx, "INV-AUTO-ORGMISMATCH-1")
        portal = CountingPortal()
        monkeypatch.setattr("app.consumers.get_irp_client", lambda: portal)

        async with session.begin():
            rows = await _requests(session, org_id)
        envelope = _envelope(rows[0])
        tampered_event_id = str(uuid.uuid4())
        tampered_envelope = {
            **envelope,
            "event_id": tampered_event_id,
            "payload": {**envelope["payload"], "organization_id": str(other_org)},
        }

        await file_requested_invoice(tampered_envelope)  # must not raise

        assert portal.calls == []
        async with session.begin():
            processed = await session.get(ProcessedEvent, uuid.UUID(tampered_event_id))
        assert processed is not None


# --------------------------------------------------------------------- terminal rejection


class TestPortalRejectionIsTerminal:
    async def test_a_rejection_is_recorded_once_and_never_retried(
        self, session, org_id, monkeypatch
    ) -> None:
        """The portal read the document and refused it. The same bytes will be refused again, so
        there is no retry: one `failed` row, one non-retryable event, nothing queued."""
        ctx = admin_ctx(org_id)
        async with session.begin():
            await _register(session, ctx)
            customer = await _customer(session, ctx)
            # No HSN on the line — the portal's 2227.
            invoice = await services.create_invoice(
                session,
                ctx,
                _invoice_payload(
                    customer.id,
                    "INV-AUTO-REJECT-1",
                    lines=[
                        InvoiceLineCreate(
                            description="Goods", quantity=1, unit_price_minor=25_00_000
                        )
                    ],
                ),
            )
        portal = CountingPortal()
        monkeypatch.setattr("app.consumers.get_irp_client", lambda: portal)

        async with session.begin():
            rows = await _requests(session, org_id)
        await file_requested_invoice(_envelope(rows[0]))

        async with session.begin():
            filings = await selectors.filings_for_invoice(session, org_id, invoice.id)
            queued = await _requests(session, org_id)
            failed = (
                (
                    await session.execute(
                        select(OutboxEvent).where(
                            OutboxEvent.organization_id == org_id,
                            OutboxEvent.event_type == services.EVT_INVOICE_FILING_FAILED,
                        )
                    )
                )
                .scalars()
                .all()
            )

        assert len(portal.calls) == 1
        assert [f.status for f in filings] == ["failed"]
        assert filings[0].error_code == "2227"
        assert len(queued) == 1, "a rejection was queued for retry"
        assert failed[0].payload["retryable"] is False
        assert failed[0].payload["terminal"] is True

    async def test_an_organization_with_no_registration_is_announced_not_recorded(
        self, session, org_id, monkeypatch
    ) -> None:
        """Nothing was attempted, so nothing goes in the filing ledger — the same rule the manual
        path follows. But nobody asked and nobody is waiting, so silence would be worse: the
        obligation goes onto the outbox instead, and the read model still says `required`."""
        ctx = admin_ctx(org_id)
        async with session.begin():
            customer = await _customer(session, ctx)
            invoice = await services.create_invoice(
                session, ctx, _invoice_payload(customer.id, "INV-AUTO-NOREG-1")
            )
            invoice_id = invoice.id
        portal = CountingPortal()
        monkeypatch.setattr("app.consumers.get_irp_client", lambda: portal)

        async with session.begin():
            rows = await _requests(session, org_id)
        await file_requested_invoice(_envelope(rows[0]))

        async with session.begin():
            filings = await selectors.filings_for_invoice(session, org_id, invoice_id)
            failed = (
                (
                    await session.execute(
                        select(OutboxEvent).where(
                            OutboxEvent.organization_id == org_id,
                            OutboxEvent.event_type == services.EVT_INVOICE_FILING_FAILED,
                        )
                    )
                )
                .scalars()
                .all()
            )
        assert portal.calls == []
        assert filings == []
        assert len(failed) == 1
        assert failed[0].payload["error_code"] == domain.ERR_NO_REGISTRATION
        assert failed[0].payload["retryable"] is False


# --------------------------------------------------------------------- outcomes, not exceptions


class TestTheHandlerDoesNotRaise:
    """A handler that raises gets the message redelivered once and then dead-lettered. A
    dead-lettered event in a compliance path is an incident, so the business situations that are
    errors on the manual route are named outcomes here."""

    async def test_a_request_for_a_vanished_invoice_is_an_outcome(self, session, org_id) -> None:
        ctx = admin_ctx(org_id)
        async with session.begin():
            await _register(session, ctx)
            result = await services.autofile_invoice(
                session, ctx, StubIrpClient(), invoice_id=uuid.uuid4()
            )
        assert result.outcome == "unknown_invoice"

    async def test_a_non_reportable_document_is_an_outcome(self, session, org_id) -> None:
        ctx = admin_ctx(org_id)
        async with session.begin():
            await _register(session, ctx)
            customer = await _customer(session, ctx, gstin=None, name="Walk-in")
            invoice = await services.create_invoice(
                session, ctx, _invoice_payload(customer.id, "INV-AUTO-NR-1")
            )
            result = await services.autofile_invoice(
                session, ctx, StubIrpClient(), invoice_id=invoice.id
            )
        assert result.outcome == "not_required"

    async def test_every_outcome_is_in_the_declared_vocabulary(self) -> None:
        """A guard against a typo in an outcome nobody matches on: the set is declared, so a new
        one has to be added deliberately."""
        assert "already_filed" in services.FILING_OUTCOMES
        assert "retry_scheduled" in services.FILING_OUTCOMES
        assert "exhausted" in services.FILING_OUTCOMES


# --------------------------------------------------------------------- the RLS net


class TestTheGucIsLoadBearing:
    async def test_the_filing_write_fails_without_the_tenant_guc(
        self, unbound_app_session, org_id
    ) -> None:
        """The consumer runs as `opsgrids_finance` (NOBYPASSRLS) and `einvoice_filings` is under a
        forced policy whose `WITH CHECK` is false with no tenant bound. This is exactly what the
        per-message `session_scope(organization_id=...)` prevents — proven by omitting it."""
        with pytest.raises(Exception, match="row-level security"):
            async with unbound_app_session.begin():
                unbound_app_session.add(
                    EInvoiceFiling(
                        organization_id=org_id,
                        invoice_id=uuid.uuid4(),
                        status="pending",
                        attempted_at=datetime.now(UTC) - timedelta(minutes=1),
                    )
                )
                await unbound_app_session.flush()

    async def test_another_tenant_sees_none_of_the_filings(
        self, session, org_id, monkeypatch
    ) -> None:
        """The consumer binds one tenant per message, so a filing it writes is that tenant's and
        nobody else's — asserted through the same app role the service runs as."""
        ctx = admin_ctx(org_id)
        await _finalize(session, ctx, "INV-AUTO-RLS-1")
        monkeypatch.setattr("app.consumers.get_irp_client", CountingPortal)
        async with session.begin():
            rows = await _requests(session, org_id)
        await file_requested_invoice(_envelope(rows[0]))

        stranger = uuid.uuid4()
        async with session.begin():
            mine = (
                await session.execute(
                    select(func.count())
                    .select_from(EInvoiceFiling)
                    .where(EInvoiceFiling.organization_id == org_id)
                )
            ).scalar_one()
            theirs = await selectors.latest_filings(session, stranger, [rows[0].aggregate_id])
        assert mine == 1
        assert theirs == ({}, {})
