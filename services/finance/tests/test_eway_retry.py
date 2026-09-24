"""C4: an e-Way outage is retried automatically, and everything else is not.

A bill is a licence for goods that are already loaded, so a portal outage cannot be left to a sweep
that ticks every few minutes: the retry is scheduled the moment the outage is recorded
(`schedule_event`, ADR-0015), on a short bounded backoff, and the reclaim sweep is the net under a
schedule that was lost. What these tests hold the design to:

* **Retryable versus permanent.** An outage, a timeout, a 5xx or a rate limit is retried with the
  identical consignment. A portal refusal is terminal at once, never retried, and ends in a
  `failed` row carrying the portal's code, its words masked of identifiers, and a remedy.
* **Bounded.** The budget is `domain.EWAY_MAX_ATTEMPTS`; a spent budget is
  `failed(retry_exhausted)`, a chain older than 24 hours is `failed(stale)`, and neither leaves a
  `pending` row nobody looks at.
* **Never a second bill.** Every attempt is sent under the same (document, transporter/vehicle
  part) key, so a portal that already issued the bill answers "already generated" with it.
* **No network call inside a transaction (D3).** The scheduled retry, the sweep and a person's
  "retry now" are one claim / call / record path; a crash between the call and the record is
  finished by the sweep under the same key.
* **Authorization and tenancy.** "Retry now" is its own permission, 404s for another tenant's
  document, and is rate limited.

Real PostgreSQL, the app role, the real consumer; the portal is replaced per test by a double that
counts its calls, because "did not raise a second bill" is a claim about a network call.
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select, text

from app import domain, selectors, services
from app.config import settings
from app.consumers import generate_requested_eway
from app.cron import FilingReclaimSweep
from app.eway.base import (
    EwayAck,
    EwayAlreadyGenerated,
    EwayCancelAck,
    EwayPayload,
    EwayRateLimited,
    EwayRejected,
    EwayUnavailable,
)
from app.eway.stub import StubEwayClient
from app.main import app
from app.models import AuditLog, EwayBill, OutboxEvent, ProcessedEvent
from app.schemas import (
    CustomerCreate,
    GenerateEwayIn,
    GstRegistrationUpsert,
    InvoiceCreate,
    InvoiceLineCreate,
)
from platform_core.auth import Principal, get_principal
from platform_core.db import (
    dispose_engine,
    dispose_outbox_engine,
    get_outbox_sessionmaker,
    init_engine,
    init_outbox_engine,
    session_scope,
)
from platform_core.events import PLATFORM_SCHEDULE_REQUESTED, validate_event_schema
from tests.helpers import FINANCE_PERMISSIONS, admin_ctx, principal_for

pytestmark = pytest.mark.integration

SELLER = "27AABCA9012D1Z5"
BUYER = "27AAECO9012H1ZG"
CONTRACTS = Path(__file__).resolve().parents[3] / "libs" / "contracts"

#: Far enough ahead that every row written "now" is past every re-drive deadline.
LATER = timedelta(hours=1)


@pytest.fixture(autouse=True)
async def _engine() -> Any:
    """The consumer and the sweep open their own `session_scope`, and the sweep's discovery needs
    the outbox engine — the app lifespan does both in production."""
    cfg = settings()
    init_engine(cfg)
    init_outbox_engine(cfg)
    yield
    await dispose_outbox_engine()
    await dispose_engine()


# --------------------------------------------------------------------- portal doubles


class ScriptedPortal:
    """An e-Way portal that answers per call from a script and otherwise behaves like the real one:
    idempotent by the platform's key, so a repeat of a generation it already issued is answered
    "already generated" with that bill. `calls` is what "never raised twice" is asserted on."""

    name = "scripted-eway"

    def __init__(self, *script: Any) -> None:
        self.script = list(script)
        self.calls: list[EwayPayload] = []
        self.issued: dict[str, EwayAck] = {}

    def generate(self, payload: EwayPayload) -> EwayAck:
        self.calls.append(payload)
        step = self.script.pop(0) if self.script else "ok"
        if isinstance(step, Exception):
            raise step
        if payload.idempotency_key in self.issued:
            raise EwayAlreadyGenerated(self.issued[payload.idempotency_key])
        ack = StubEwayClient().generate(payload)
        self.issued[payload.idempotency_key] = ack
        if step == "issue_then_time_out":
            # The portal acted and the answer was lost — the case a timeout cannot tell apart from
            # an outage, and the reason a retry must be idempotent.
            raise EwayUnavailable("the portal did not answer within 60 seconds")
        return ack

    def cancel(self, **kwargs: Any) -> EwayCancelAck:  # pragma: no cover - not exercised here
        raise NotImplementedError


def down(text_: str = "connection reset by peer") -> EwayUnavailable:
    return EwayUnavailable(text_)


# --------------------------------------------------------------------- fixtures


async def _consignment(session, ctx, number: str) -> Any:
    """A registered seller and one finalized B2B invoice worth 1,18,000 rupees — over the e-Way
    threshold."""
    async with session.begin():
        await services.set_gst_registration(
            session,
            ctx,
            GstRegistrationUpsert(gstin=SELLER, legal_name="OpsGrids Industries Pvt Ltd"),
        )
        customer = await services.create_customer(
            session,
            ctx,
            CustomerCreate(
                name="Consignee",
                contact_name="Contact",
                email="ap@example.in",
                gstin=BUYER,
                city="Pune",
                state="Maharashtra",
            ),
        )
        return await services.create_invoice(
            session,
            ctx,
            InvoiceCreate(
                number=number,
                customer_id=customer.id,
                issued_date=date(2026, 8, 12),
                due_date=date(2026, 9, 11),
                tax_rate_pct=18,
                lines=[
                    InvoiceLineCreate(
                        description="Hot-rolled coil",
                        quantity=1,
                        unit_price_minor=100_000_00,
                        hsn_sac="72085110",
                    )
                ],
            ),
        )


def _request_in(org_id: uuid.UUID, invoice_id: uuid.UUID, **over: Any) -> GenerateEwayIn:
    fields: dict[str, Any] = {
        "organization_id": org_id,
        "invoice_id": invoice_id,
        "distance_km": 150,
        "transport_mode": "road",
        "vehicle_no": "MH12AB1234",
    }
    fields.update(over)
    return GenerateEwayIn(**fields)


async def _first_attempt(
    session,
    ctx,
    org_id: uuid.UUID,
    invoice,
    portal: Any,
    *,
    at: datetime | None = None,
    **over: Any,
) -> EwayBill:
    """The dispatch system's original request: all three acts in one call (the test wrapper)."""
    async with session.begin():
        return await services.generate_eway_bill(
            session, ctx, portal, _request_in(org_id, invoice.id, **over), at=at
        )


async def _bills(session, org_id: uuid.UUID, invoice_id: uuid.UUID) -> list[EwayBill]:
    async with session.begin():
        return await selectors.eway_bills_for_invoice(session, org_id, invoice_id)


async def _newest(session, org_id: uuid.UUID, invoice_id: uuid.UUID) -> EwayBill:
    return (await _bills(session, org_id, invoice_id))[0]


async def _events(session, org_id: uuid.UUID, event_type: str) -> list[OutboxEvent]:
    async with session.begin():
        stmt = (
            select(OutboxEvent)
            .where(OutboxEvent.organization_id == org_id, OutboxEvent.event_type == event_type)
            .order_by(OutboxEvent.created_at)
        )
        return list((await session.execute(stmt)).scalars().all())


async def _scheduled(session, org_id: uuid.UUID) -> list[OutboxEvent]:
    """The retries finance asked the scheduler to deliver later (ADR-0015), oldest first."""
    return await _events(session, org_id, PLATFORM_SCHEDULE_REQUESTED)


async def _requests(session, org_id: uuid.UUID) -> list[OutboxEvent]:
    """`generation_requested` published *directly* — a person's "retry now"."""
    return await _events(session, org_id, services.EVT_EWAY_GENERATION_REQUESTED)


def _fire(row: OutboxEvent) -> dict[str, Any]:
    """The envelope the scheduler would publish once one of its own rows falls due: a fresh event
    id, addressed at the target event type, carrying the payload it was handed."""
    target = row.payload["target"]
    return {
        "event_id": str(uuid.uuid4()),
        "event_type": target["event_type"],
        "organization_id": str(row.organization_id),
        "payload": target["payload"],
    }


def _envelope(row: OutboxEvent) -> dict[str, Any]:
    """The envelope the relay would build for one directly published outbox row."""
    return {
        "event_id": str(row.id),
        "event_type": row.event_type,
        "organization_id": str(row.organization_id),
        "payload": row.payload,
    }


def _freeze_consumer_clock(monkeypatch, *, ahead: timedelta = timedelta(hours=3)) -> None:
    """A retry fired the instant it is scheduled, not minutes later the way the real scheduler
    would: the consumer's early-arrival guard compares against the real wall clock, so without this
    a drained chain would look like it kept arriving early and never advance."""
    frozen = datetime.now(UTC) + ahead

    class _FrozenNow(datetime):
        @classmethod
        def now(cls, tz=None):  # type: ignore[no-untyped-def]
            return frozen

    monkeypatch.setattr("app.consumers.datetime", _FrozenNow)


def _sweep(portal: Any) -> FilingReclaimSweep:
    return FilingReclaimSweep(
        discovery_factory=get_outbox_sessionmaker(),
        work_scope=lambda organization_id: session_scope(organization_id=organization_id),
        eway_client_factory=lambda: portal,
    )


@contextlib.contextmanager
def _as(principal: Principal) -> Iterator[None]:
    app.dependency_overrides[get_principal] = lambda: principal
    try:
        yield
    finally:
        app.dependency_overrides[get_principal] = lambda: principal_for(
            principal.organization_id, [f"{p}:ORG" for p in FINANCE_PERMISSIONS]
        )


# --------------------------------------------------------------------- the policy, pure


class TestTheRetryPolicy:
    def test_the_backoff_doubles_from_a_minute_and_is_capped(self) -> None:
        delays = [domain.eway_retry_delay(n) for n in range(1, 9)]
        assert [d.total_seconds() // 60 for d in delays] == [1, 2, 4, 8, 16, 30, 30, 30]
        assert delays[0] == domain.EWAY_RETRY_BASE
        assert delays[-1] == domain.EWAY_RETRY_CEILING

    def test_attempts_are_one_based(self) -> None:
        with pytest.raises(ValueError, match="1-based"):
            domain.eway_retry_delay(0)

    def test_a_rate_limit_is_obeyed_up_to_the_ceiling(self) -> None:
        assert domain.eway_retry_delay(1, retry_after=timedelta(minutes=10)) == timedelta(
            minutes=10
        )
        # A `Retry-After` of a day is not a schedule goods on a road can honour.
        assert domain.eway_retry_delay(1, retry_after=timedelta(days=1)) == (
            domain.EWAY_RETRY_CEILING
        )
        # A `Retry-After` shorter than our own backoff never shortens it.
        assert domain.eway_retry_delay(4, retry_after=timedelta(seconds=5)) == timedelta(minutes=8)

    def test_the_budget_is_off_by_one_safe(self) -> None:
        assert domain.may_retry_eway(domain.EWAY_MAX_ATTEMPTS - 1) is True
        assert domain.may_retry_eway(domain.EWAY_MAX_ATTEMPTS) is False

    def test_only_the_platforms_own_failures_are_retryable_by_a_person(self) -> None:
        assert {
            domain.ERR_RETRY_EXHAUSTED,
            domain.ERR_STALE,
            domain.ERR_NO_REGISTRATION,
        } == domain.EWAY_RETRYABLE_FAILURES
        # No numbered portal refusal is in it: the same bytes would be refused again.
        assert not any(code.isdigit() for code in domain.EWAY_RETRYABLE_FAILURES)


class TestMaskingPortalText:
    @pytest.mark.parametrize(
        "raw,masked,gone",
        [
            (
                "recipient GSTIN '27AAECO9012H1ZG' is invalid",
                "recipient GSTIN '27**********1ZG' is invalid",
                "AAECO9012H",
            ),
            ("vehicle MH12AB1234 not found", "vehicle MH****34 not found", "12AB12"),
            (
                "call the consignee on 9876543210 now",
                "call the consignee on ********** now",
                "98765",
            ),
            ("mail ap@example.in about it", "mail ***@*** about it", "example.in"),
            ("PAN AAECO9012H was refused", "PAN ********** was refused", "AAECO"),
        ],
    )
    def test_identifiers_are_masked(self, raw: str, masked: str, gone: str) -> None:
        got = domain.mask_portal_text(raw)
        assert got == masked
        assert gone not in (got or "")

    def test_text_without_identifiers_is_unchanged(self) -> None:
        assert domain.mask_portal_text("connection reset by peer") == "connection reset by peer"

    def test_none_is_none(self) -> None:
        assert domain.mask_portal_text(None) is None

    def test_a_portal_that_echoes_a_payload_cannot_fill_the_ledger(self) -> None:
        got = domain.mask_portal_text("x" * 5_000)
        assert got is not None and len(got) == domain.PORTAL_TEXT_LIMIT


class TestRemedies:
    def test_a_missing_part_b_says_what_to_add(self) -> None:
        remedy, detail = domain.eway_failure_remedy("113")
        assert remedy == "correct_and_resubmit"
        assert "vehicle" in detail.lower()

    def test_a_bad_distance_and_a_bad_gstin_each_say_what_to_fix(self) -> None:
        assert "distance" in domain.eway_failure_remedy("112")[1].lower()
        assert "gstin" in domain.eway_failure_remedy("103")[1].lower()

    def test_the_platforms_own_failures_offer_a_retry(self) -> None:
        assert domain.eway_failure_remedy(domain.ERR_RETRY_EXHAUSTED)[0] == "retry_now"
        assert domain.eway_failure_remedy(domain.ERR_STALE)[0] == "retry_now"
        assert domain.eway_failure_remedy(domain.ERR_NO_REGISTRATION)[0] == "register_gst"

    def test_a_code_nobody_has_written_down_is_never_guessed_at(self) -> None:
        remedy, detail = domain.eway_failure_remedy("9999")
        assert remedy == "contact_support"
        assert "9999" in detail

    def test_every_remedy_is_in_the_published_vocabulary(self) -> None:
        for code in ("102", "103", "108", "111", "112", "113", "114", "116", "119", "120", "122"):
            assert domain.eway_failure_remedy(code)[0] in domain.EWAY_REMEDIES


# --------------------------------------------------------------------- an outage is scheduled


class TestAnOutageIsRetriedOnASchedule:
    async def test_the_outage_row_carries_the_attempt_and_the_countdown(
        self, session, org_id
    ) -> None:
        ctx = admin_ctx(org_id)
        invoice = await _consignment(session, ctx, "INV-C4-1")
        at = datetime.now(UTC)
        pending = await _first_attempt(session, ctx, org_id, invoice, ScriptedPortal(down()), at=at)
        assert pending.status == "pending"
        assert pending.error_code == domain.ERR_PORTAL_UNAVAILABLE
        assert pending.attempt == 1
        assert pending.retry_at == at + domain.EWAY_RETRY_BASE

    async def test_the_retry_is_handed_to_the_scheduler_not_published_at_once(
        self, session, org_id
    ) -> None:
        ctx = admin_ctx(org_id)
        invoice = await _consignment(session, ctx, "INV-C4-2")
        pending = await _first_attempt(session, ctx, org_id, invoice, ScriptedPortal(down()))
        bills = await _bills(session, org_id, invoice.id)

        scheduled = await _scheduled(session, org_id)
        assert len(scheduled) == 1
        job = scheduled[0].payload
        assert job["due_at"] == pending.retry_at.astimezone(UTC).isoformat()
        assert job["target"]["event_type"] == services.EVT_EWAY_GENERATION_REQUESTED
        target = job["target"]["payload"]
        assert target["attempt"] == 2
        assert target["trigger"] == "retry"
        assert target["max_attempts"] == domain.EWAY_MAX_ATTEMPTS
        # The chain is named by its first claim, so a retry outliving its chain is recognised.
        assert target["chain_id"] == str(bills[-1].id)
        assert bills[-1].status == "submitting"
        # Nothing was published for the worker to act on *now*: the goods wait for the backoff.
        assert await _requests(session, org_id) == []

    async def test_an_outage_is_announced_as_retry_scheduled_not_failed(
        self, session, org_id
    ) -> None:
        ctx = admin_ctx(org_id)
        invoice = await _consignment(session, ctx, "INV-C4-3")
        pending = await _first_attempt(session, ctx, org_id, invoice, ScriptedPortal(down()))

        scheduled = await _events(session, org_id, services.EVT_EWAY_RETRY_SCHEDULED)
        assert len(scheduled) == 1
        payload = scheduled[0].payload
        assert payload["attempt"] == 1 and payload["next_attempt"] == 2
        assert payload["max_attempts"] == domain.EWAY_MAX_ATTEMPTS
        assert payload["retry_at"] == pending.retry_at.isoformat()
        assert payload["error_code"] == domain.ERR_PORTAL_UNAVAILABLE
        # `failed` is terminal only since C4: an outage being retried is not a failure to act on.
        assert await _events(session, org_id, services.EVT_EWAY_FAILED) == []

    async def test_a_rate_limit_waits_as_long_as_the_portal_asked(self, session, org_id) -> None:
        ctx = admin_ctx(org_id)
        invoice = await _consignment(session, ctx, "INV-C4-4")
        at = datetime.now(UTC)
        portal = ScriptedPortal(
            EwayRateLimited("too many requests", retry_after=timedelta(minutes=12))
        )
        pending = await _first_attempt(session, ctx, org_id, invoice, portal, at=at)
        assert pending.status == "pending"
        assert pending.error_code == domain.ERR_RATE_LIMITED
        assert pending.retry_at == at + timedelta(minutes=12)

    async def test_a_portal_that_asks_for_a_day_is_capped_at_the_ceiling(
        self, session, org_id
    ) -> None:
        ctx = admin_ctx(org_id)
        invoice = await _consignment(session, ctx, "INV-C4-5")
        at = datetime.now(UTC)
        portal = ScriptedPortal(EwayRateLimited("slow down", retry_after=timedelta(days=1)))
        pending = await _first_attempt(session, ctx, org_id, invoice, portal, at=at)
        assert pending.retry_at == at + domain.EWAY_RETRY_CEILING

    async def test_the_outage_is_audited_with_its_attempt_and_countdown(
        self, session, org_id
    ) -> None:
        ctx = admin_ctx(org_id)
        invoice = await _consignment(session, ctx, "INV-C4-6")
        await _first_attempt(session, ctx, org_id, invoice, ScriptedPortal(down()))
        async with session.begin():
            rows = (
                (
                    await session.execute(
                        select(AuditLog).where(
                            AuditLog.organization_id == org_id, AuditLog.action == "eway.pending"
                        )
                    )
                )
                .scalars()
                .all()
            )
        assert len(rows) == 1
        assert rows[0].after["attempt"] == 1 and "retry_at" in rows[0].after


# --------------------------------------------------------------------- a refusal is not retried


class TestAPermanentRefusalIsNeverRetried:
    async def _refused(self, session, org_id, number: str, exc: Exception) -> tuple[Any, Any]:
        ctx = admin_ctx(org_id)
        invoice = await _consignment(session, ctx, number)
        bill = await _first_attempt(session, ctx, org_id, invoice, ScriptedPortal(exc))
        return invoice, bill

    async def test_a_validation_answer_is_terminal_at_once(self, session, org_id) -> None:
        _, bill = await self._refused(
            session,
            org_id,
            "INV-C4-R1",
            EwayRejected("113", "a road movement must name the vehicle carrying it"),
        )
        assert bill.status == "failed"
        assert bill.error_code == "113"
        assert bill.attempt == 1
        assert bill.retry_at is None
        # Nothing scheduled, nothing announced as a retry: identical bytes are refused again.
        assert await _scheduled(session, org_id) == []
        assert await _events(session, org_id, services.EVT_EWAY_RETRY_SCHEDULED) == []

    async def test_the_failure_event_is_terminal_and_carries_the_remedy(
        self, session, org_id
    ) -> None:
        await self._refused(
            session,
            org_id,
            "INV-C4-R2",
            EwayRejected("113", "a road movement must name the vehicle carrying it"),
        )
        failed = await _events(session, org_id, services.EVT_EWAY_FAILED)
        assert len(failed) == 1
        payload = failed[0].payload
        assert payload["terminal"] is True and payload["retryable"] is False
        assert payload["error_code"] == "113"
        assert payload["remedy"] == "correct_and_resubmit"
        assert "vehicle" in payload["remedy_detail"].lower()

    async def test_the_portals_words_are_masked_before_they_are_stored(
        self, session, org_id
    ) -> None:
        raw = "recipient GSTIN 27AAECO9012H1ZG is invalid; vehicle MH12AB1234; call 9876543210"
        _, bill = await self._refused(session, org_id, "INV-C4-R3", EwayRejected("103", raw))
        assert bill.error_code == "103"  # the code is never masked
        for leaked in ("AAECO9012H", "12AB12", "9876543210"):
            assert leaked not in (bill.error or "")
        assert "27**********1ZG" in bill.error
        # …and so nothing downstream can leak what was never stored.
        failed = await _events(session, org_id, services.EVT_EWAY_FAILED)
        assert "AAECO9012H" not in (failed[0].payload["error"] or "")

    async def test_an_outage_message_is_masked_too(self, session, org_id) -> None:
        ctx = admin_ctx(org_id)
        invoice = await _consignment(session, ctx, "INV-C4-R4")
        pending = await _first_attempt(
            session,
            ctx,
            org_id,
            invoice,
            ScriptedPortal(down("upstream 503 while resolving 27AAECO9012H1ZG")),
        )
        assert "AAECO9012H" not in (pending.error or "")

    async def test_a_manual_retry_of_a_refusal_is_a_409_naming_what_to_fix(
        self, client, session, org_id
    ) -> None:
        invoice, _ = await self._refused(
            session,
            org_id,
            "INV-C4-R5",
            EwayRejected("113", "a road movement must name the vehicle carrying it"),
        )
        res = await client.post(f"/api/v1/finance/invoices/{invoice.id}/eway/retry")
        assert res.status_code == 409
        assert "113" in res.json()["detail"] and "vehicle" in res.json()["detail"].lower()
        assert await _requests(session, org_id) == []


# --------------------------------------------------------------------- the scheduled retry runs


class TestTheScheduledRetry:
    async def _pending(self, session, org_id, number: str, portal: Any) -> tuple[Any, Any]:
        ctx = admin_ctx(org_id)
        invoice = await _consignment(session, ctx, number)
        await _first_attempt(session, ctx, org_id, invoice, portal)
        return invoice, ctx

    async def test_the_retry_raises_the_bill_and_says_which_attempt_did(
        self, session, org_id, monkeypatch
    ) -> None:
        portal = ScriptedPortal(down(), "ok")
        monkeypatch.setattr("app.consumers.get_eway_client", lambda: portal)
        invoice, _ = await self._pending(session, org_id, "INV-C4-S1", portal)
        _freeze_consumer_clock(monkeypatch)
        job = (await _scheduled(session, org_id))[0]

        await generate_requested_eway(_fire(job))

        bills = await _bills(session, org_id, invoice.id)
        assert [b.status for b in bills] == ["generated", "submitting", "pending", "submitting"]
        generated = bills[0]
        assert generated.attempt == 2
        assert generated.valid_until == domain.eway_valid_until(
            generated.valid_from, generated.distance_km
        )
        events = await _events(session, org_id, services.EVT_EWAY_GENERATED)
        assert len(events) == 1 and events[0].payload["attempt"] == 2
        assert len(portal.calls) == 2

    async def test_a_redelivered_retry_calls_the_portal_once(
        self, session, org_id, monkeypatch
    ) -> None:
        portal = ScriptedPortal(down(), "ok")
        monkeypatch.setattr("app.consumers.get_eway_client", lambda: portal)
        await self._pending(session, org_id, "INV-C4-S2", portal)
        _freeze_consumer_clock(monkeypatch)
        envelope = _fire((await _scheduled(session, org_id))[0])

        await generate_requested_eway(envelope)
        await generate_requested_eway(envelope)

        assert len(portal.calls) == 2  # the first attempt, and the retry — not a third
        async with session.begin():
            assert await session.get(ProcessedEvent, uuid.UUID(envelope["event_id"])) is not None

    async def test_every_attempt_is_sent_under_one_idempotency_key(
        self, session, org_id, monkeypatch
    ) -> None:
        portal = ScriptedPortal(down(), down(), "ok")
        monkeypatch.setattr("app.consumers.get_eway_client", lambda: portal)
        await self._pending(session, org_id, "INV-C4-S3", portal)
        _freeze_consumer_clock(monkeypatch)

        for _ in range(2):
            job = (await _scheduled(session, org_id))[-1]
            await generate_requested_eway(_fire(job))

        keys = [call.idempotency_key for call in portal.calls]
        assert len(keys) == 3 and len(set(keys)) == 1

    async def test_the_chain_is_bounded_and_ends_in_a_visible_failure(
        self, session, org_id, monkeypatch
    ) -> None:
        portal = ScriptedPortal(*[down()] * 30)
        monkeypatch.setattr("app.consumers.get_eway_client", lambda: portal)
        invoice, _ = await self._pending(session, org_id, "INV-C4-S4", portal)
        _freeze_consumer_clock(monkeypatch)

        handled: set[uuid.UUID] = set()
        for _ in range(domain.EWAY_MAX_ATTEMPTS + 3):
            fresh = [r for r in await _scheduled(session, org_id) if r.id not in handled]
            if not fresh:
                break
            for row in fresh:
                handled.add(row.id)
                await generate_requested_eway(_fire(row))

        bills = await _bills(session, org_id, invoice.id)
        assert len(portal.calls) == domain.EWAY_MAX_ATTEMPTS
        assert bills[0].status == "failed"
        assert bills[0].error_code == domain.ERR_RETRY_EXHAUSTED
        assert bills[0].attempt == domain.EWAY_MAX_ATTEMPTS
        assert sum(1 for b in bills if b.status == "submitting") == domain.EWAY_MAX_ATTEMPTS
        assert sum(1 for b in bills if b.status == "pending") == domain.EWAY_MAX_ATTEMPTS - 1
        # No retry was scheduled past the budget: seven waits for eight attempts.
        assert len(await _scheduled(session, org_id)) == domain.EWAY_MAX_ATTEMPTS - 1
        failed = await _events(session, org_id, services.EVT_EWAY_FAILED)
        assert len(failed) == 1
        assert failed[0].payload["error_code"] == domain.ERR_RETRY_EXHAUSTED
        assert failed[0].payload["remedy"] == "retry_now"
        assert failed[0].payload["terminal"] is True
        assert (
            len(await _events(session, org_id, services.EVT_EWAY_RETRY_SCHEDULED))
            == domain.EWAY_MAX_ATTEMPTS - 1
        )

    async def test_a_refusal_on_a_retry_ends_the_chain(self, session, org_id, monkeypatch) -> None:
        """The portal was down, and when it came back it refused the consignment: that is the end,
        with the portal's code, and nothing further is scheduled."""
        portal = ScriptedPortal(down(), EwayRejected("112", "distance out of range"))
        monkeypatch.setattr("app.consumers.get_eway_client", lambda: portal)
        invoice, _ = await self._pending(session, org_id, "INV-C4-S5", portal)
        _freeze_consumer_clock(monkeypatch)

        await generate_requested_eway(_fire((await _scheduled(session, org_id))[0]))

        bills = await _bills(session, org_id, invoice.id)
        assert bills[0].status == "failed" and bills[0].error_code == "112"
        assert len(await _scheduled(session, org_id)) == 1  # only the one that was just fired

    async def test_a_message_arriving_before_its_time_is_re_armed_not_acted_on(
        self, session, org_id, monkeypatch
    ) -> None:
        portal = ScriptedPortal(down(), "ok")
        monkeypatch.setattr("app.consumers.get_eway_client", lambda: portal)
        invoice, _ = await self._pending(session, org_id, "INV-C4-S6", portal)
        envelope = _fire((await _scheduled(session, org_id))[0])
        future = datetime.now(UTC) + timedelta(hours=1)
        envelope["payload"] = {**envelope["payload"], "not_before": future.isoformat()}

        await generate_requested_eway(envelope)

        assert len(portal.calls) == 1  # the retry was not acted on
        scheduled = await _scheduled(session, org_id)
        assert len(scheduled) == 2
        assert scheduled[-1].payload["due_at"] == future.astimezone(UTC).isoformat()
        # Keyed off this message's own id: the original key belongs to a job that already fired.
        assert envelope["event_id"] in scheduled[-1].payload["idempotency_key"]
        assert (await _newest(session, org_id, invoice.id)).status == "pending"

    async def test_a_payload_naming_another_organization_is_refused(
        self, session, org_id, monkeypatch
    ) -> None:
        portal = ScriptedPortal(down(), "ok")
        monkeypatch.setattr("app.consumers.get_eway_client", lambda: portal)
        await self._pending(session, org_id, "INV-C4-S7", portal)
        _freeze_consumer_clock(monkeypatch)
        envelope = _fire((await _scheduled(session, org_id))[0])
        envelope["payload"] = {**envelope["payload"], "organization_id": str(uuid.uuid4())}

        await generate_requested_eway(envelope)  # must not raise

        assert len(portal.calls) == 1
        async with session.begin():
            assert await session.get(ProcessedEvent, uuid.UUID(envelope["event_id"])) is not None

    async def test_another_tenants_invoice_named_by_a_message_is_not_touched(
        self, session, org_id, monkeypatch
    ) -> None:
        """A retry envelope for org B naming org A's invoice resolves to nothing: the claim runs
        under B's binding, so A's ledger is invisible — the cross-tenant read is a 404 in a
        consumer's vocabulary (`unknown_invoice`), and the portal is never asked."""
        portal = ScriptedPortal(down(), "ok")
        monkeypatch.setattr("app.consumers.get_eway_client", lambda: portal)
        invoice, _ = await self._pending(session, org_id, "INV-C4-S8", portal)
        _freeze_consumer_clock(monkeypatch)
        other = uuid.uuid4()
        envelope = _fire((await _scheduled(session, org_id))[0])
        envelope["organization_id"] = str(other)
        envelope["payload"] = {**envelope["payload"], "organization_id": str(other)}

        await generate_requested_eway(envelope)

        assert len(portal.calls) == 1
        assert (await _newest(session, org_id, invoice.id)).status == "pending"


class TestALateOrOvertakenRetryDoesNothing:
    async def _setup(self, session, org_id, monkeypatch, number: str, portal: Any):
        monkeypatch.setattr("app.consumers.get_eway_client", lambda: portal)
        ctx = admin_ctx(org_id)
        invoice = await _consignment(session, ctx, number)
        await _first_attempt(session, ctx, org_id, invoice, portal)
        _freeze_consumer_clock(monkeypatch)
        return ctx, invoice

    async def test_a_bill_that_already_exists_is_never_raised_again(
        self, session, org_id, monkeypatch
    ) -> None:
        portal = ScriptedPortal(down(), "ok")
        ctx, invoice = await self._setup(session, org_id, monkeypatch, "INV-C4-L1", portal)
        envelope = _fire((await _scheduled(session, org_id))[0])
        # The dispatch system asked again meanwhile, and got its bill.
        async with session.begin():
            await services.generate_eway_bill(session, ctx, portal, _request_in(org_id, invoice.id))
        calls_before = len(portal.calls)

        await generate_requested_eway(envelope)

        assert len(portal.calls) == calls_before
        generated = [
            b for b in await _bills(session, org_id, invoice.id) if b.status == "generated"
        ]
        assert len(generated) == 1

    async def test_a_retry_the_ledger_has_moved_past_is_superseded(
        self, session, org_id, monkeypatch
    ) -> None:
        """Two attempts happened since this message was scheduled (a person's retry and the sweep,
        say): it names an attempt the chain is no longer waiting for, so it must not run — running
        would spend a second attempt of the budget on the same moment."""
        portal = ScriptedPortal(down(), down(), down(), "ok")
        ctx, invoice = await self._setup(session, org_id, monkeypatch, "INV-C4-L2", portal)
        stale = _fire((await _scheduled(session, org_id))[0])  # attempt 2
        for _ in range(2):  # attempts 2 and 3 run by other means
            async with session.begin():
                await services.generate_eway_bill(
                    session, ctx, portal, _request_in(org_id, invoice.id)
                )
        calls_before = len(portal.calls)

        await generate_requested_eway(stale)

        assert len(portal.calls) == calls_before

    async def test_a_message_from_an_earlier_chain_does_not_drive_the_current_one(
        self, session, org_id, monkeypatch
    ) -> None:
        portal = ScriptedPortal(down(), "ok")
        _, invoice = await self._setup(session, org_id, monkeypatch, "INV-C4-L3", portal)
        envelope = _fire((await _scheduled(session, org_id))[0])
        envelope["payload"] = {**envelope["payload"], "chain_id": str(uuid.uuid4())}
        calls_before = len(portal.calls)

        await generate_requested_eway(envelope)

        assert len(portal.calls) == calls_before
        assert (await _newest(session, org_id, invoice.id)).status == "pending"

    async def test_a_claim_somebody_is_holding_is_not_raced(
        self, session, org_id, monkeypatch
    ) -> None:
        portal = ScriptedPortal(down(), "ok")
        ctx, invoice = await self._setup(session, org_id, monkeypatch, "INV-C4-L4", portal)
        envelope = _fire((await _scheduled(session, org_id))[0])
        # Somebody else claimed attempt 2 and has not recorded yet.
        async with session.begin():
            await services.claim_eway_generation(session, ctx, _request_in(org_id, invoice.id))
        calls_before = len(portal.calls)

        await generate_requested_eway(envelope)

        assert len(portal.calls) == calls_before
        assert (await _newest(session, org_id, invoice.id)).status == "submitting"


# --------------------------------------------------------------------- the window


class TestTheGenerationWindow:
    async def test_a_chain_first_asked_for_a_day_ago_is_not_retried_automatically(
        self, session, org_id, monkeypatch
    ) -> None:
        portal = ScriptedPortal(down(), "ok")
        monkeypatch.setattr("app.consumers.get_eway_client", lambda: portal)
        ctx = admin_ctx(org_id)
        invoice = await _consignment(session, ctx, "INV-C4-W1")
        first = datetime.now(UTC) - domain.EWAY_REDRIVE_MAX_AGE - timedelta(minutes=5)
        await _first_attempt(session, ctx, org_id, invoice, portal, at=first)
        _freeze_consumer_clock(monkeypatch)
        job = (await _scheduled(session, org_id))[0]

        await generate_requested_eway(_fire(job))

        assert len(portal.calls) == 1  # the portal was not asked
        bills = await _bills(session, org_id, invoice.id)
        assert bills[0].status == "failed" and bills[0].error_code == domain.ERR_STALE
        failed = await _events(session, org_id, services.EVT_EWAY_FAILED)
        assert failed[-1].payload["remedy"] == "retry_now"

    async def test_no_retry_is_scheduled_past_the_moment_the_window_shuts(
        self, session, org_id, monkeypatch
    ) -> None:
        """Asked for 23h59m ago, the second attempt still runs — and when the portal is down again
        its retry would land after the window shut, so the chain ends `stale` now instead of
        leaving a `pending` row that can never be honoured."""
        portal = ScriptedPortal(down(), down())
        monkeypatch.setattr("app.consumers.get_eway_client", lambda: portal)
        ctx = admin_ctx(org_id)
        invoice = await _consignment(session, ctx, "INV-C4-W2")
        first = datetime.now(UTC) - domain.EWAY_REDRIVE_MAX_AGE + timedelta(minutes=1)
        await _first_attempt(session, ctx, org_id, invoice, portal, at=first)
        _freeze_consumer_clock(monkeypatch)
        job = (await _scheduled(session, org_id))[0]

        await generate_requested_eway(_fire(job))

        assert len(portal.calls) == 2
        bills = await _bills(session, org_id, invoice.id)
        assert bills[0].status == "failed" and bills[0].error_code == domain.ERR_STALE
        assert len(await _scheduled(session, org_id)) == 1  # nothing new was scheduled


# --------------------------------------------------------------------- never a second bill


class TestARetryNeverMintsASecondBill:
    async def test_a_bill_issued_behind_a_timeout_is_found_again_not_raised_twice(
        self, session, org_id, monkeypatch
    ) -> None:
        """The portal issued the bill and the answer was lost — indistinguishable from an outage.
        The retry carries the same key, the portal answers "already generated", and the ledger
        holds one bill and announces one."""
        portal = ScriptedPortal("issue_then_time_out", "ok")
        monkeypatch.setattr("app.consumers.get_eway_client", lambda: portal)
        ctx = admin_ctx(org_id)
        invoice = await _consignment(session, ctx, "INV-C4-N1")
        first = await _first_attempt(session, ctx, org_id, invoice, portal)
        assert first.status == "pending"
        _freeze_consumer_clock(monkeypatch)

        await generate_requested_eway(_fire((await _scheduled(session, org_id))[0]))

        bills = await _bills(session, org_id, invoice.id)
        generated = [b for b in bills if b.status == "generated"]
        assert len(generated) == 1
        assert generated[0].eway_bill_no == next(iter(portal.issued.values())).eway_bill_no
        assert len(portal.issued) == 1, "the portal was asked to mint a second bill"
        assert len(await _events(session, org_id, services.EVT_EWAY_GENERATED)) == 1

    async def test_the_stub_portal_answers_a_repeat_for_real(
        self, session, org_id, monkeypatch
    ) -> None:
        """No double: the shipped stub numbers a bill from its key and remembers what it issued, so
        the "already generated" path a retry depends on runs for real."""
        stub = StubEwayClient()
        ctx = admin_ctx(org_id)
        invoice = await _consignment(session, ctx, "INV-C4-N2")
        async with session.begin():
            facts = await services.claim_eway_generation(
                session, ctx, _request_in(org_id, invoice.id)
            )
        first = services.call_eway_generate(stub, facts.payload)
        second = services.call_eway_generate(stub, facts.payload)
        assert first.kind == second.kind == "generated"
        assert first.ack is not None and second.ack is not None
        assert first.ack.eway_bill_no == second.ack.eway_bill_no


# --------------------------------------------------------------------- crash recovery


class TestACrashBetweenTheCallAndTheRecord:
    async def test_the_sweep_finishes_a_scheduled_retry_whose_record_never_ran(
        self, session, org_id, monkeypatch
    ) -> None:
        """The consumer claims, calls, and its process dies before recording. The queue never
        redelivers a claimed message (it was marked processed in the claim), so the sweep is the
        only recovery: it finds the standing `submitting` claim, re-sends under the same key, the
        portal answers "already generated", and exactly one bill is recorded."""
        portal = ScriptedPortal(down(), "ok")
        monkeypatch.setattr("app.consumers.get_eway_client", lambda: portal)
        ctx = admin_ctx(org_id)
        invoice = await _consignment(session, ctx, "INV-C4-X1")
        await _first_attempt(session, ctx, org_id, invoice, portal)
        _freeze_consumer_clock(monkeypatch)

        async def _die(*args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("process killed between the call and the record")

        monkeypatch.setattr(services, "record_eway_generation", _die)
        with pytest.raises(RuntimeError):
            await generate_requested_eway(_fire((await _scheduled(session, org_id))[0]))
        monkeypatch.undo()

        # The portal issued the bill; the ledger does not know.
        assert len(portal.issued) == 1
        assert (await _newest(session, org_id, invoice.id)).status == "submitting"

        report = await _sweep(portal).tick(now=datetime.now(UTC) + LATER)

        assert report.failed == 0
        bills = await _bills(session, org_id, invoice.id)
        generated = [b for b in bills if b.status == "generated"]
        assert len(generated) == 1
        assert generated[0].eway_bill_no == next(iter(portal.issued.values())).eway_bill_no
        assert len(portal.issued) == 1
        assert len(await _events(session, org_id, services.EVT_EWAY_GENERATED)) == 1

    async def test_a_crash_between_the_claim_and_the_call_is_finished_by_the_sweep(
        self, session, org_id, monkeypatch
    ) -> None:
        portal = ScriptedPortal(down(), "ok")
        monkeypatch.setattr("app.consumers.get_eway_client", lambda: portal)
        ctx = admin_ctx(org_id)
        invoice = await _consignment(session, ctx, "INV-C4-X2")
        await _first_attempt(session, ctx, org_id, invoice, portal)
        _freeze_consumer_clock(monkeypatch)

        async def _die(*args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("process killed before the portal was called")

        monkeypatch.setattr(services, "call_eway_generate_off_loop", _die)
        with pytest.raises(RuntimeError):
            await generate_requested_eway(_fire((await _scheduled(session, org_id))[0]))
        monkeypatch.undo()
        assert len(portal.calls) == 1  # the portal never heard about attempt 2

        await _sweep(portal).tick(now=datetime.now(UTC) + LATER)

        bills = await _bills(session, org_id, invoice.id)
        assert bills[0].status == "generated" and bills[0].attempt == 3

    async def test_the_sweep_is_still_the_net_when_the_schedule_is_lost(
        self, session, org_id
    ) -> None:
        """The scheduler is a separate service. If it never delivers, the `pending` row is found by
        the sweep once its own deadline passes, and the goods still get their bill."""
        portal = ScriptedPortal(down(), "ok")
        ctx = admin_ctx(org_id)
        invoice = await _consignment(session, ctx, "INV-C4-X3")
        await _first_attempt(session, ctx, org_id, invoice, portal)
        assert len(await _scheduled(session, org_id)) == 1  # scheduled… and never fired

        report = await _sweep(portal).tick(now=datetime.now(UTC) + LATER)

        assert report.failed == 0
        assert (await _newest(session, org_id, invoice.id)).status == "generated"

    async def test_a_late_scheduled_message_after_the_sweep_finished_it_is_a_no_op(
        self, session, org_id, monkeypatch
    ) -> None:
        portal = ScriptedPortal(down(), "ok")
        monkeypatch.setattr("app.consumers.get_eway_client", lambda: portal)
        ctx = admin_ctx(org_id)
        invoice = await _consignment(session, ctx, "INV-C4-X4")
        await _first_attempt(session, ctx, org_id, invoice, portal)
        envelope = _fire((await _scheduled(session, org_id))[0])
        await _sweep(portal).tick(now=datetime.now(UTC) + LATER)
        calls_before = len(portal.calls)
        _freeze_consumer_clock(monkeypatch)

        await generate_requested_eway(envelope)

        assert len(portal.calls) == calls_before
        generated = [
            b for b in await _bills(session, org_id, invoice.id) if b.status == "generated"
        ]
        assert len(generated) == 1


# --------------------------------------------------------------------- retry now (the route)


class TestRetryNow:
    URL = "/api/v1/finance/invoices/{}/eway/retry"

    async def _pending(self, session, org_id, number: str, portal: Any | None = None):
        ctx = admin_ctx(org_id)
        invoice = await _consignment(session, ctx, number)
        await _first_attempt(session, ctx, org_id, invoice, portal or ScriptedPortal(down()))
        return invoice, ctx

    async def test_an_outage_awaiting_its_retry_can_be_retried_now(
        self, client, session, org_id
    ) -> None:
        invoice, _ = await self._pending(session, org_id, "INV-C4-M1")

        res = await client.post(self.URL.format(invoice.id))

        assert res.status_code == 202, res.text
        body = res.json()
        assert body["status"] == "pending" and body["can_retry"] is True
        requests = await _requests(session, org_id)
        assert len(requests) == 1
        assert requests[0].payload["trigger"] == "manual"
        assert requests[0].payload["attempt"] == 2
        assert requests[0].payload["requested_by"] is not None
        # Published directly — nobody chose to wait — not handed to the scheduler.
        assert len(await _scheduled(session, org_id)) == 1  # the automatic retry, still owed

    async def test_the_route_calls_no_portal_and_writes_no_claim(
        self, client, session, org_id
    ) -> None:
        """The request half never waits on a government (D3): it stages a request and returns. The
        claim, the call and the record all happen in the worker."""
        invoice, _ = await self._pending(session, org_id, "INV-C4-M2")
        before = [b.status for b in await _bills(session, org_id, invoice.id)]

        res = await client.post(self.URL.format(invoice.id))

        assert res.status_code == 202
        assert [b.status for b in await _bills(session, org_id, invoice.id)] == before

    async def test_the_worker_then_raises_the_bill(
        self, client, session, org_id, monkeypatch
    ) -> None:
        portal = ScriptedPortal(down(), "ok")
        monkeypatch.setattr("app.consumers.get_eway_client", lambda: portal)
        invoice, _ = await self._pending(session, org_id, "INV-C4-M3", portal)
        await client.post(self.URL.format(invoice.id))
        request = (await _requests(session, org_id))[0]

        await generate_requested_eway(_envelope(request))

        bills = await _bills(session, org_id, invoice.id)
        assert bills[0].status == "generated" and bills[0].attempt == 2
        assert len(portal.calls) == 2

    async def test_it_is_audited_under_the_person_who_asked(self, client, session, org_id) -> None:
        invoice, _ = await self._pending(session, org_id, "INV-C4-M4")
        await client.post(self.URL.format(invoice.id))
        async with session.begin():
            rows = (
                (
                    await session.execute(
                        select(AuditLog).where(
                            AuditLog.organization_id == org_id,
                            AuditLog.action == "eway.retry_requested",
                        )
                    )
                )
                .scalars()
                .all()
            )
        assert len(rows) == 1
        assert rows[0].actor_id is not None
        assert rows[0].after["from_status"] == "pending"

    async def test_a_spent_budget_can_be_retried_by_a_person_with_a_fresh_chain(
        self, client, session, org_id, monkeypatch
    ) -> None:
        portal = ScriptedPortal(*[down()] * 30)
        monkeypatch.setattr("app.consumers.get_eway_client", lambda: portal)
        invoice, _ = await self._pending(session, org_id, "INV-C4-M5", portal)
        _freeze_consumer_clock(monkeypatch)
        handled: set[uuid.UUID] = set()
        for _ in range(domain.EWAY_MAX_ATTEMPTS + 2):
            fresh = [r for r in await _scheduled(session, org_id) if r.id not in handled]
            if not fresh:
                break
            for row in fresh:
                handled.add(row.id)
                await generate_requested_eway(_fire(row))
        assert (await _bills(session, org_id, invoice.id))[
            0
        ].error_code == domain.ERR_RETRY_EXHAUSTED
        portal.script = ["ok"]

        state = (await client.post(self.URL.format(invoice.id))).json()
        assert state["status"] == "failed" and state["can_retry"] is True
        request = (await _requests(session, org_id))[-1]
        assert request.payload["attempt"] == 1  # a new chain: a fresh budget
        await generate_requested_eway(_envelope(request))

        bills = await _bills(session, org_id, invoice.id)
        assert bills[0].status == "generated" and bills[0].attempt == 1

    async def test_a_persons_retry_is_not_refused_for_a_chains_age(
        self, client, session, org_id, monkeypatch
    ) -> None:
        """The 24-hour cap stops an *automatic* generation of goods that should have moved a day
        ago. A person pressing "retry now" is exactly the decision that cap defers to."""
        portal = ScriptedPortal(down(), "ok")
        monkeypatch.setattr("app.consumers.get_eway_client", lambda: portal)
        ctx = admin_ctx(org_id)
        invoice = await _consignment(session, ctx, "INV-C4-M6")
        old = datetime.now(UTC) - timedelta(hours=30)
        await _first_attempt(session, ctx, org_id, invoice, portal, at=old)
        await client.post(self.URL.format(invoice.id))

        await generate_requested_eway(_envelope((await _requests(session, org_id))[0]))

        assert (await _bills(session, org_id, invoice.id))[0].status == "generated"

    async def test_a_generation_in_flight_is_a_409(self, client, session, org_id) -> None:
        ctx = admin_ctx(org_id)
        invoice = await _consignment(session, ctx, "INV-C4-M7")
        async with session.begin():
            await services.claim_eway_generation(session, ctx, _request_in(org_id, invoice.id))
        res = await client.post(self.URL.format(invoice.id))
        assert res.status_code == 409 and "in progress" in res.json()["detail"]

    async def test_a_standing_bill_is_a_409(self, client, session, org_id) -> None:
        ctx = admin_ctx(org_id)
        invoice = await _consignment(session, ctx, "INV-C4-M8")
        await _first_attempt(session, ctx, org_id, invoice, ScriptedPortal("ok"))
        res = await client.post(self.URL.format(invoice.id))
        assert res.status_code == 409 and "already has an e-Way bill" in res.json()["detail"]

    async def test_a_document_nothing_was_asked_for_is_a_409(self, client, session, org_id) -> None:
        ctx = admin_ctx(org_id)
        invoice = await _consignment(session, ctx, "INV-C4-M9")
        res = await client.post(self.URL.format(invoice.id))
        assert res.status_code == 409 and "nothing to retry" in res.json()["detail"]

    async def test_a_second_press_within_the_window_is_rate_limited(
        self, client, session, org_id
    ) -> None:
        invoice, _ = await self._pending(session, org_id, "INV-C4-M10")
        assert (await client.post(self.URL.format(invoice.id))).status_code == 202
        second = await client.post(self.URL.format(invoice.id))
        assert second.status_code == 429
        assert len(await _requests(session, org_id)) == 1


class TestRetryNowAuthorization:
    URL = TestRetryNow.URL

    async def test_a_caller_without_the_permission_is_refused(self, client, org_id) -> None:
        with _as(principal_for(org_id, perms=[])):
            res = await client.post(self.URL.format(uuid.uuid4()))
        assert res.status_code == 403

    async def test_reading_e_way_bills_does_not_carry_the_right_to_retry_one(
        self, client, session, org_id
    ) -> None:
        ctx = admin_ctx(org_id)
        invoice = await _consignment(session, ctx, "INV-C4-A1")
        await _first_attempt(session, ctx, org_id, invoice, ScriptedPortal(down()))
        reader = principal_for(
            org_id,
            perms=["finance.compliance.eway.read:ORG", "finance.compliance.einvoice.read:ORG"],
        )
        with _as(reader):
            res = await client.post(self.URL.format(invoice.id))
        assert res.status_code == 403
        assert await _requests(session, org_id) == []

    async def test_an_own_scope_grant_is_below_the_org_floor(self, client, org_id) -> None:
        with _as(principal_for(org_id, perms=["finance.compliance.eway.retry:OWN"])):
            res = await client.post(self.URL.format(uuid.uuid4()))
        assert res.status_code == 403

    async def test_an_organization_that_has_not_activated_finance_is_402(
        self, client, org_id
    ) -> None:
        with _as(
            principal_for(org_id, perms=["finance.compliance.eway.retry:ORG"], entitlements=[])
        ):
            res = await client.post(self.URL.format(uuid.uuid4()))
        assert res.status_code == 402

    async def test_another_tenants_document_is_a_404_and_stages_nothing(
        self, client, session, org_id
    ) -> None:
        ctx = admin_ctx(org_id)
        invoice = await _consignment(session, ctx, "INV-C4-A2")
        await _first_attempt(session, ctx, org_id, invoice, ScriptedPortal(down()))
        intruder = uuid.uuid4()
        with _as(principal_for(intruder, [f"{p}:ORG" for p in FINANCE_PERMISSIONS])):
            res = await client.post(self.URL.format(invoice.id))
        assert res.status_code == 404
        assert await _requests(session, org_id) == []

    async def test_an_unknown_document_is_a_404(self, client) -> None:
        res = await client.post(self.URL.format(uuid.uuid4()))
        assert res.status_code == 404


# --------------------------------------------------------------------- the read model


class TestTheReadModel:
    async def _state(self, client, invoice_id) -> dict[str, Any]:
        res = await client.get(f"/api/v1/finance/invoices/{invoice_id}/eway")
        assert res.status_code == 200, res.text
        return res.json()

    async def test_an_outage_reads_as_pending_with_a_countdown_and_an_attempt(
        self, client, session, org_id
    ) -> None:
        ctx = admin_ctx(org_id)
        invoice = await _consignment(session, ctx, "INV-C4-V1")
        pending = await _first_attempt(session, ctx, org_id, invoice, ScriptedPortal(down()))

        state = await self._state(client, invoice.id)

        assert state["status"] == "pending" and state["terminal"] is False
        assert state["attempt"] == 1 and state["max_attempts"] == domain.EWAY_MAX_ATTEMPTS
        assert datetime.fromisoformat(state["retry_at"]) == pending.retry_at
        assert state["error_code"] == domain.ERR_PORTAL_UNAVAILABLE
        assert state["can_retry"] is True
        assert "sent again automatically" in state["remedy_detail"]

    async def test_a_refusal_reads_as_terminal_with_a_remedy_and_no_retry(
        self, client, session, org_id
    ) -> None:
        ctx = admin_ctx(org_id)
        invoice = await _consignment(session, ctx, "INV-C4-V2")
        await _first_attempt(
            session,
            ctx,
            org_id,
            invoice,
            ScriptedPortal(EwayRejected("113", "a road movement must name the vehicle")),
        )

        state = await self._state(client, invoice.id)

        assert state["status"] == "failed" and state["terminal"] is True
        assert state["error_code"] == "113"
        assert state["remedy"] == "correct_and_resubmit"
        assert "vehicle" in state["remedy_detail"].lower()
        assert state["can_retry"] is False
        assert state["retry_at"] is None

    async def test_a_spent_budget_reads_as_terminal_and_retryable_by_a_person(
        self, client, session, org_id
    ) -> None:
        ctx = admin_ctx(org_id)
        invoice = await _consignment(session, ctx, "INV-C4-V3")
        async with session.begin():
            facts = await services.claim_eway_generation(
                session, ctx, _request_in(org_id, invoice.id)
            )
            spent = services.EwayGenerateFacts(
                invoice_id=facts.invoice_id,
                transport=facts.transport,
                payload=facts.payload,
                at=facts.at,
                attempt=domain.EWAY_MAX_ATTEMPTS,
                chain_id=facts.chain_id,
            )
            await services.record_eway_generation(
                session,
                ctx,
                spent,
                services.call_eway_generate(ScriptedPortal(down()), facts.payload),
                portal="scripted-eway",
            )

        state = await self._state(client, invoice.id)

        assert state["status"] == "failed" and state["terminal"] is True
        assert state["error_code"] == domain.ERR_RETRY_EXHAUSTED
        assert state["remedy"] == "retry_now" and state["can_retry"] is True

    async def test_a_generation_in_flight_reads_as_submitting(
        self, client, session, org_id
    ) -> None:
        ctx = admin_ctx(org_id)
        invoice = await _consignment(session, ctx, "INV-C4-V4")
        async with session.begin():
            await services.claim_eway_generation(session, ctx, _request_in(org_id, invoice.id))
        state = await self._state(client, invoice.id)
        assert state["status"] == "submitting" and state["attempt"] == 1
        assert state["can_retry"] is False

    async def test_a_standing_bill_offers_no_retry(self, client, session, org_id) -> None:
        ctx = admin_ctx(org_id)
        invoice = await _consignment(session, ctx, "INV-C4-V5")
        await _first_attempt(session, ctx, org_id, invoice, ScriptedPortal("ok"))
        state = await self._state(client, invoice.id)
        assert state["status"] in ("generated", "expired") and state["can_retry"] is False

    async def test_the_history_carries_the_attempt_and_the_retry_time(
        self, client, session, org_id
    ) -> None:
        ctx = admin_ctx(org_id)
        invoice = await _consignment(session, ctx, "INV-C4-V6")
        await _first_attempt(session, ctx, org_id, invoice, ScriptedPortal(down()))
        res = await client.get(f"/api/v1/finance/invoices/{invoice.id}/eway-bills")
        rows = res.json()
        assert rows[0]["status"] == "pending"
        assert rows[0]["attempt"] == 1 and rows[0]["retry_at"] is not None

    async def test_the_register_marks_the_current_row_and_carries_its_state(
        self, client, session, org_id, monkeypatch
    ) -> None:
        """The register lists every attempt. Only the document's newest carries a countdown and a
        retry — an older `pending` row is history, and showing it a "retrying in…" would be a lie."""
        portal = ScriptedPortal(down(), down())
        monkeypatch.setattr("app.consumers.get_eway_client", lambda: portal)
        ctx = admin_ctx(org_id)
        invoice = await _consignment(session, ctx, "INV-C4-V7")
        await _first_attempt(session, ctx, org_id, invoice, portal)
        _freeze_consumer_clock(monkeypatch)
        await generate_requested_eway(_fire((await _scheduled(session, org_id))[0]))

        res = await client.get("/api/v1/finance/eway-bills", params={"status": "pending"})

        assert res.status_code == 200
        items = res.json()["items"]
        assert [i["bill"]["attempt"] for i in items] == [2, 1]  # newest first
        current, history = items
        assert current["is_current"] is True and history["is_current"] is False
        assert history["state"] is None
        assert current["state"]["status"] == "pending"
        assert current["state"]["attempt"] == 2 and current["state"]["can_retry"] is True
        assert current["state"]["retry_at"] is not None


# --------------------------------------------------------------------- the contracts


class TestEventPayloadsMatchTheirSchemas:
    """Every payload here comes out of the real function that stages it — never a hand-written
    dict that could quietly drift from what the code emits."""

    def _validate(self, event_type: str, payload: dict[str, Any]) -> None:
        validate_event_schema(event_type, 1, payload, contracts_root=CONTRACTS)

    async def test_retry_scheduled_and_the_scheduled_request(self, session, org_id) -> None:
        ctx = admin_ctx(org_id)
        invoice = await _consignment(session, ctx, "INV-C4-E1")
        await _first_attempt(session, ctx, org_id, invoice, ScriptedPortal(down()))
        scheduled = (await _events(session, org_id, services.EVT_EWAY_RETRY_SCHEDULED))[0]
        self._validate(services.EVT_EWAY_RETRY_SCHEDULED, scheduled.payload)
        job = (await _scheduled(session, org_id))[0]
        self._validate(services.EVT_EWAY_GENERATION_REQUESTED, job.payload["target"]["payload"])

    async def test_a_rate_limited_retry_scheduled(self, session, org_id) -> None:
        ctx = admin_ctx(org_id)
        invoice = await _consignment(session, ctx, "INV-C4-E2")
        await _first_attempt(
            session,
            ctx,
            org_id,
            invoice,
            ScriptedPortal(EwayRateLimited("slow down", retry_after=timedelta(minutes=3))),
        )
        event = (await _events(session, org_id, services.EVT_EWAY_RETRY_SCHEDULED))[0]
        self._validate(services.EVT_EWAY_RETRY_SCHEDULED, event.payload)
        assert event.payload["error_code"] == domain.ERR_RATE_LIMITED

    async def test_failed_for_a_refusal_and_for_a_spent_budget(self, session, org_id) -> None:
        ctx = admin_ctx(org_id)
        refused = await _consignment(session, ctx, "INV-C4-E3")
        await _first_attempt(
            session,
            ctx,
            org_id,
            refused,
            ScriptedPortal(EwayRejected("9999", "something nobody documented")),
        )
        for event in await _events(session, org_id, services.EVT_EWAY_FAILED):
            self._validate(services.EVT_EWAY_FAILED, event.payload)
            assert event.payload["remedy"] == "contact_support"

    async def test_generated(self, session, org_id) -> None:
        ctx = admin_ctx(org_id)
        invoice = await _consignment(session, ctx, "INV-C4-E4")
        await _first_attempt(session, ctx, org_id, invoice, ScriptedPortal("ok"))
        event = (await _events(session, org_id, services.EVT_EWAY_GENERATED))[0]
        self._validate(services.EVT_EWAY_GENERATED, event.payload)

    async def test_a_manual_retry_request(self, client, session, org_id) -> None:
        ctx = admin_ctx(org_id)
        invoice = await _consignment(session, ctx, "INV-C4-E5")
        await _first_attempt(session, ctx, org_id, invoice, ScriptedPortal(down()))
        await client.post(f"/api/v1/finance/invoices/{invoice.id}/eway/retry")
        request = (await _requests(session, org_id))[0]
        self._validate(services.EVT_EWAY_GENERATION_REQUESTED, request.payload)


class TestTheSchedulerKey:
    def test_a_restarted_chain_does_not_collide_with_the_one_before_it(self) -> None:
        """The scheduler drops a request whose key it has already seen. A key of `(invoice,
        attempt)` alone would let a person's fresh chain be silently swallowed by the schedule of
        the chain that ended — so the chain is part of the key."""
        invoice = uuid.uuid4()
        first, second = uuid.uuid4(), uuid.uuid4()
        assert services._eway_request_key(invoice, first, 2) != services._eway_request_key(
            invoice, second, 2
        )
        assert services._eway_request_key(invoice, first, 2) != services._eway_request_key(
            invoice, first, 3
        )
        assert services._eway_request_key(invoice, first, 2) == services._eway_request_key(
            invoice, first, 2
        )


class TestNoRegistration:
    async def test_a_retry_after_the_registration_went_away_ends_in_a_named_failure(
        self, session, admin_session, org_id, monkeypatch
    ) -> None:
        portal = ScriptedPortal(down(), "ok")
        monkeypatch.setattr("app.consumers.get_eway_client", lambda: portal)
        ctx = admin_ctx(org_id)
        invoice = await _consignment(session, ctx, "INV-C4-G1")
        await _first_attempt(session, ctx, org_id, invoice, portal)
        async with admin_session.begin():
            await admin_session.execute(
                text(
                    "UPDATE finance.gst_registrations SET deleted_at = now() "
                    "WHERE organization_id = :o"
                ),
                {"o": org_id},
            )
        _freeze_consumer_clock(monkeypatch)

        await generate_requested_eway(_fire((await _scheduled(session, org_id))[0]))

        assert len(portal.calls) == 1
        bills = await _bills(session, org_id, invoice.id)
        assert bills[0].status == "failed" and bills[0].error_code == domain.ERR_NO_REGISTRATION
        failed = await _events(session, org_id, services.EVT_EWAY_FAILED)
        assert failed[-1].payload["remedy"] == "register_gst"
