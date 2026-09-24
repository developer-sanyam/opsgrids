"""C2: IMS decisions are pushed back to GSTN — recorded, queued, sent on request, reconciled.

A decision recorded here is the platform's own position, appended to its own ledger. Telling GSTN is
a second act with a second failure mode, and everything below holds it to the properties that make
that safe:

* **A decision is never edited.** The push is its own row — a state machine (`queued → submitting →
  pushed | failed | conflict`), with the decision beside it untouched; a change of mind is a new
  decision with a new version, a new key, and a new push.
* **Nobody sends without asking.** Recording queues; only a person holding
  `finance.compliance.ims.push` sends, and nothing — the consumer, the sweep, a redelivery — sends
  a decision nobody asked for.
* **Claim, call, record (D3).** No transaction is open across the portal call; a crash between the
  call and the record is finished by the sweep under the same key, and the portal's "already
  recorded" is the success it is; a late recorder whose lease lapsed writes nothing.
* **Bounded.** Batches within the portal's limit, a backoff after an outage, a budget that ends in a
  visible `failed(retry_exhausted)`, a per-document refusal that is never retried.
* **Reconciled.** The next sync compares what the portal holds with what was pushed; a mismatch is
  `drift`, flagged once, cleared when it agrees again.
* **ITC follows the decision, not the push.** A document that is not currently accepted is not in
  GSTR-3B's Table 4A, whatever became of its push.

Real PostgreSQL, the app role, the real consumer and sweep; the portal is the shipped stub (or a
double around it that counts and fails on cue), because "sent once" is a claim about a call.
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import event, select, text
from sqlalchemy.exc import DBAPIError

from app import domain, selectors, services
from app.config import settings
from app.consumers import push_requested_ims
from app.cron import FilingReclaimSweep
from app.deps import get_ims_client
from app.ims.base import (
    ImsPushItem,
    ImsPushRejected,
    ImsPushResult,
    ImsRateLimited,
    ImsUnavailable,
)
from app.ims.stub import StubImsClient
from app.main import app
from app.models import (
    AuditLog,
    ImsDecision,
    ImsDocument,
    ImsPush,
    ItcLedgerEntry,
    OutboxEvent,
    ProcessedEvent,
    PurchaseDocument,
)
from app.schemas import (
    CustomerCreate,
    GenerateReturnIn,
    GstRegistrationUpsert,
    InvoiceCreate,
    InvoiceLineCreate,
    PurchaseDocumentCreate,
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
from platform_core.events import validate_event_schema
from tests.helpers import FINANCE_PERMISSIONS, admin_ctx, principal_for, service_token_headers

pytestmark = pytest.mark.integration

SELLER = "27AABCA9012D1Z5"
BUYER = "27AAECO9012H1ZG"
PERIOD = "2026-08"
QUEUE = "/api/v1/finance/ims/documents"
DECISIONS = "/api/v1/finance/ims/decisions"
PUSHES = "/api/v1/finance/ims/pushes"
SUMMARY = "/api/v1/finance/ims/pushes/summary"
SYNC = "/internal/v1/ims/sync"
CONTRACTS = Path(__file__).resolve().parents[3] / "libs" / "contracts"

#: The stub portal's suppliers (see `app/ims/stub.py`).
MERIDIAN = "24AABCM1234F1Z3"  # Gujarat, inter-state -> IGST. Documents 0001 and 0004.
KANAKA = "33AAACK5678G1ZH"  # Tamil Nadu -> IGST. Document 0002.
ORBIT = "27AAECO9012H1ZG"  # Maharashtra -> CGST+SGST. Document 0005.

#: Far enough ahead that every claim written "now" is past its lease and every backoff.
LATER = timedelta(hours=2)


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


class ScriptedIms(StubImsClient):
    """The shipped stub, with a script for the first push calls: an exception to raise, or
    `"record_then_time_out"` for the portal recording the batch and the answer being lost — the case
    a timeout cannot tell apart from an outage, and the reason a repeat must be idempotent. Anything
    else (or an exhausted script) is the stub's own honest answer."""

    def __init__(self, *script: Any) -> None:
        super().__init__()
        self.script = list(script)

    def push_ims_decisions(self, *, gstin: str, period: str, decisions: Any) -> list[ImsPushResult]:
        step = self.script.pop(0) if self.script else None
        if isinstance(step, Exception):
            self.push_calls.append((period, [d.idempotency_key for d in decisions]))
            raise step
        results = super().push_ims_decisions(gstin=gstin, period=period, decisions=decisions)
        if step == "record_then_time_out":
            raise ImsUnavailable("the portal did not answer within 60 seconds")
        return results


class CannedIms(StubImsClient):
    """A portal whose per-document answers are decided by `answers[source_ref]`."""

    def __init__(self, answers: dict[str, ImsPushResult]) -> None:
        super().__init__()
        self.answers = answers

    def push_ims_decisions(self, *, gstin: str, period: str, decisions: Any) -> list[ImsPushResult]:
        self.push_calls.append((period, [d.idempotency_key for d in decisions]))
        return [
            self.answers.get(d.source_ref, ImsPushResult(d.source_ref, "recorded"))
            for d in decisions
        ]


def down(text_: str = "connection reset by peer") -> ImsUnavailable:
    return ImsUnavailable(text_)


# --------------------------------------------------------------------- fixtures


async def _register(session, ctx) -> None:
    await services.set_gst_registration(
        session,
        ctx,
        GstRegistrationUpsert(gstin=SELLER, legal_name="OpsGrids Industries Pvt Ltd"),
    )


def _purchase(supplier_gstin: str, number: str, taxable: int, rate: float, name: str):
    return PurchaseDocumentCreate(
        supplier_gstin=supplier_gstin,
        number=number,
        document_date=date(2026, 8, 1),
        taxable_minor=taxable,
        tax_rate_pct=rate,
        supplier_name=name,
    )


async def _book(session, ctx, *, with_liability: bool = False) -> None:
    """A registered organization with the purchase book the stub's documents reconcile against."""
    await _register(session, ctx)
    await services.record_purchase_document(
        session, ctx, _purchase(MERIDIAN, "MT-4471", 4_50_000, 18, "Meridian")
    )
    await services.record_purchase_document(
        session, ctx, _purchase(ORBIT, "OL-9002", 90_000, 5, "Orbit Logistics")
    )
    if with_liability:
        customer = await services.create_customer(
            session,
            ctx,
            CustomerCreate(
                name="Buyer",
                contact_name="Contact",
                email="ap@example.in",
                gstin=BUYER,
                city="Pune",
                state="Maharashtra",
            ),
        )
        await services.create_invoice(
            session,
            ctx,
            InvoiceCreate(
                number="INV-PUSH-1",
                customer_id=customer.id,
                issued_date=date(2026, 8, 12),
                due_date=date(2026, 9, 11),
                tax_rate_pct=18,
                lines=[
                    InvoiceLineCreate(
                        description="Hot-rolled coil",
                        quantity=1,
                        unit_price_minor=10_00_000,
                        hsn_sac="72085110",
                    )
                ],
            ),
        )


async def _sync(client, org_id, period: str = PERIOD) -> Any:
    return await client.post(
        SYNC,
        headers=service_token_headers(),
        json={"organization_id": str(org_id), "period": period},
    )


async def _queue(
    client, session, org_id, *, with_liability: bool = False
) -> dict[str, ImsDocument]:
    """The organization, its book, one sync — and the documents by portal reference."""
    async with session.begin():
        await _book(session, admin_ctx(org_id), with_liability=with_liability)
    res = await _sync(client, org_id)
    assert res.status_code == 200, res.text
    async with session.begin():
        docs = await selectors.all_ims_documents(session, org_id)
    return {doc.source_ref: doc for doc in docs}


def _ref(n: int) -> str:
    return f"{PERIOD}/{n:04d}"


async def _decide(client, doc: ImsDocument, action: str, note: str | None = None, **extra: Any):
    res = await client.post(
        f"{QUEUE}/{doc.id}/decisions", json={"action": action, "note": note, **extra}
    )
    assert res.status_code == 201, res.text
    return res.json()


async def _pushes(session, org_id, *, current_only: bool = False) -> list[ImsPush]:
    async with session.begin():
        stmt = (
            select(ImsPush)
            .where(ImsPush.organization_id == org_id)
            .order_by(ImsPush.source_ref, ImsPush.version)
        )
        rows = list((await session.execute(stmt)).scalars().all())
    return [r for r in rows if r.is_current] if current_only else rows


async def _push_of(session, org_id, ref: str) -> ImsPush:
    rows = [r for r in await _pushes(session, org_id) if r.source_ref == ref and r.is_current]
    assert len(rows) == 1, f"expected one current push for {ref}, found {len(rows)}"
    return rows[0]


async def _events(session, org_id, event_type: str) -> list[OutboxEvent]:
    async with session.begin():
        stmt = (
            select(OutboxEvent)
            .where(OutboxEvent.organization_id == org_id, OutboxEvent.event_type == event_type)
            .order_by(OutboxEvent.created_at)
        )
        return list((await session.execute(stmt)).scalars().all())


async def _audits(session, org_id, action: str) -> list[AuditLog]:
    async with session.begin():
        stmt = (
            select(AuditLog)
            .where(AuditLog.organization_id == org_id, AuditLog.action == action)
            .order_by(AuditLog.at)
        )
        return list((await session.execute(stmt)).scalars().all())


async def _ask(client, period: str = PERIOD, **body: Any) -> Any:
    return await client.post(PUSHES, json={"period": period, **body})


def _envelope(row: OutboxEvent) -> dict[str, Any]:
    return {
        "event_id": str(row.id),
        "event_type": row.event_type,
        "organization_id": str(row.organization_id),
        "payload": row.payload,
    }


async def _send(session, org_id, portal: Any, monkeypatch, *, index: int = -1) -> None:
    """The worker acting on the newest `ims_push.requested` a person's request staged."""
    monkeypatch.setattr("app.consumers.get_ims_client", lambda: portal)
    requests = await _events(session, org_id, services.EVT_IMS_PUSH_REQUESTED)
    await push_requested_ims(_envelope(requests[index]))


def _sweep(portal: Any) -> FilingReclaimSweep:
    return FilingReclaimSweep(
        discovery_factory=get_outbox_sessionmaker(),
        work_scope=lambda organization_id: session_scope(organization_id=organization_id),
        ims_client_factory=lambda: portal,
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


# --------------------------------------------------------------------- the vocabulary, pure


class TestTheVocabulary:
    def test_no_action_is_a_decision_that_returns_the_document_to_pending(self) -> None:
        assert "no_action" in domain.IMS_ACTIONS
        assert domain.IMS_ACTION_STATUS["no_action"] == "pending"
        # An explicit no-action is never an accept: nothing here claims credit nobody accepted.
        assert domain.IMS_ACTION_STATUS["no_action"] != "accepted"

    def test_the_portal_has_its_own_words_for_the_actions(self) -> None:
        assert domain.ims_portal_action("accept") == "accepted"
        assert domain.ims_portal_action("reject") == "rejected"
        assert domain.ims_portal_action("pending_action") == "pending"
        assert domain.ims_portal_action("no_action") == "no_action"
        with pytest.raises(ValueError, match="not an IMS action"):
            domain.ims_portal_action("approve")

    def test_the_key_is_period_document_and_version(self) -> None:
        key = domain.ims_push_key("2026-08", "2026-08/0001", 1)
        assert key == "ims-push:2026-08:2026-08/0001:v1"
        assert domain.ims_push_key("2026-08", "2026-08/0001", 2) != key
        assert domain.ims_push_key("2026-08", "2026-08/0002", 1) != key
        assert domain.ims_push_key("2026-09", "2026-08/0001", 1) != key
        assert domain.ims_push_key("2026-08", "2026-08/0001", 1) == key
        with pytest.raises(ValueError, match="1-based"):
            domain.ims_push_key("2026-08", "x", 0)

    def test_the_backoff_doubles_from_a_minute_and_is_capped(self) -> None:
        minutes = [domain.ims_push_retry_delay(n).total_seconds() // 60 for n in range(1, 8)]
        assert minutes == [1, 2, 4, 8, 16, 30, 30]
        with pytest.raises(ValueError, match="1-based"):
            domain.ims_push_retry_delay(0)

    def test_silence_from_the_portal_is_not_drift(self) -> None:
        assert domain.ims_is_drift(pushed_action="accept", observed=None) is False
        assert domain.ims_is_drift(pushed_action="accept", observed="accepted") is False
        assert domain.ims_is_drift(pushed_action="accept", observed="rejected") is True
        assert domain.ims_is_drift(pushed_action="pending_action", observed="pending") is False
        assert domain.ims_is_drift(pushed_action="no_action", observed="no_action") is False

    def test_a_push_lease_outlives_the_call_it_covers(self) -> None:
        """The start-up check that protects every other claim (`Settings._claims_outlive_the_call`)
        covers this one only because it is the same constant."""
        assert domain.IMS_PUSH_LEASE == domain.FILING_RECLAIM_AFTER
        assert (
            domain.IMS_PUSH_LEASE.total_seconds() >= 2 * settings().portal_call_timeout_seconds + 60
        )


# --------------------------------------------------------------------- recording queues a push


class TestRecordingAQueuesAPush:
    async def test_a_decision_queues_a_push_and_edits_nothing(
        self, client, session, org_id
    ) -> None:
        docs = await _queue(client, session, org_id)
        doc = docs[_ref(1)]
        body = await _decide(client, doc, "accept", "Goods received against PO 4471")

        push = await _push_of(session, org_id, _ref(1))
        assert push.status == "queued" and push.is_current is True
        assert push.action == "accept" and push.version == 1
        assert push.idempotency_key == domain.ims_push_key(PERIOD, _ref(1), 1)
        assert push.period == PERIOD and push.attempts == 0
        # Decided is not requested: nothing is sent until a person asks.
        assert push.requested_at is None and push.lease_until is None
        assert body["version"] == 1 and body["push"]["status"] == "queued"
        # The decision row is exactly what was decided, and the same row the ledger points at.
        async with session.begin():
            decisions = list((await session.execute(select(ImsDecision))).scalars().all())
        assert [d.id for d in decisions] == [push.decision_id]
        assert decisions[0].note == "Goods received against PO 4471"

    async def test_a_change_of_mind_is_a_new_decision_with_a_new_version_and_push(
        self, client, session, org_id
    ) -> None:
        docs = await _queue(client, session, org_id)
        doc = docs[_ref(2)]
        await _decide(client, doc, "pending_action")
        await _decide(client, doc, "reject", "Duplicate of a document already filed")

        pushes = [p for p in await _pushes(session, org_id) if p.source_ref == _ref(2)]
        assert [(p.version, p.action, p.status, p.is_current) for p in pushes] == [
            (1, "pending_action", "superseded", False),
            (2, "reject", "queued", True),
        ]
        assert len({p.idempotency_key for p in pushes}) == 2
        async with session.begin():
            assert len((await session.execute(select(ImsDecision))).scalars().all()) == 2

    async def test_a_change_of_mind_after_the_first_was_sent_keeps_that_fact(
        self, client, session, org_id, monkeypatch
    ) -> None:
        docs = await _queue(client, session, org_id)
        doc = docs[_ref(1)]
        await _decide(client, doc, "accept")
        assert (await _ask(client)).status_code == 202
        await _send(session, org_id, StubImsClient(), monkeypatch)
        assert (await _push_of(session, org_id, _ref(1))).status == "pushed"

        await _decide(client, doc, "reject", "Supplier amended the invoice")

        pushes = [p for p in await _pushes(session, org_id) if p.source_ref == _ref(1)]
        # It happened: the first push stays `pushed`, it just is not the document's answer any more.
        assert [(p.version, p.status, p.is_current) for p in pushes] == [
            (1, "pushed", False),
            (2, "queued", True),
        ]

    async def test_no_action_is_recorded_pushed_and_returns_the_document_to_pending(
        self, client, session, org_id
    ) -> None:
        docs = await _queue(client, session, org_id)
        doc = docs[_ref(1)]
        await _decide(client, doc, "accept")
        body = await _decide(
            client, doc, "no_action", "Withdrawn pending the supplier's credit note"
        )

        assert body["action"] == "no_action" and body["push"]["action"] == "no_action"
        async with session.begin():
            refreshed = await selectors.get_ims_document(session, org_id, doc.id)
        assert refreshed is not None and refreshed.status == "pending"

    async def test_a_decision_is_audited_with_its_version_and_push(
        self, client, session, org_id
    ) -> None:
        docs = await _queue(client, session, org_id)
        await _decide(client, docs[_ref(1)], "accept")
        rows = await _audits(session, org_id, "ims.decided")
        assert len(rows) == 1
        push = await _push_of(session, org_id, _ref(1))
        assert rows[0].after["version"] == 1 and rows[0].after["push_id"] == str(push.id)

    async def test_unknown_fields_are_refused_on_a_decision(self, client, session, org_id) -> None:
        docs = await _queue(client, session, org_id)
        res = await client.post(
            f"{QUEUE}/{docs[_ref(1)].id}/decisions", json={"action": "accept", "approved_by": "me"}
        )
        assert res.status_code == 422

    async def test_the_document_list_carries_where_each_push_stands(
        self, client, session, org_id
    ) -> None:
        docs = await _queue(client, session, org_id)
        await _decide(client, docs[_ref(1)], "accept")
        res = await client.get(QUEUE, params={"sort": "date_asc"})
        by_ref = {i["source_ref"]: i for i in res.json()["items"]}
        assert by_ref[_ref(1)]["push"]["status"] == "queued"
        assert by_ref[_ref(2)]["push"] is None  # nothing decided, nothing to push


# --------------------------------------------------------------------- ITC follows the decision


class TestRejectedAndHeldDocumentsAreNotInGstr3b:
    """A4's ledger posts credit only while a document's newest decision is an accept and reverses it
    on leaving; Table 4A reads the ledger. So a document that is not currently accepted is not in
    the return — as a projection of the latest decision, never by editing a purchase document."""

    async def _generate(self, session, org_id) -> Any:
        ctx = admin_ctx(org_id)
        async with session.begin():
            ret = await services.generate_return(
                session, ctx, GenerateReturnIn(period=PERIOD, return_type="gstr3b")
            )
            detail = await selectors.return_detail(session, org_id, ret)
        assert detail.sections is not None
        return detail.sections

    async def test_only_the_accepted_document_reaches_table_4a(
        self, client, session, org_id
    ) -> None:
        docs = await _queue(client, session, org_id, with_liability=True)
        async with session.begin():
            purchases_before = sorted(
                (p.id, p.taxable_minor, p.total_minor)
                for p in (await session.execute(select(PurchaseDocument))).scalars().all()
            )
        accepted = docs[_ref(1)]  # Meridian: IGST
        await _decide(client, accepted, "accept")
        await _decide(client, docs[_ref(2)], "reject", "Not our supplier")
        await _decide(client, docs[_ref(3)], "pending_action", "Awaiting the delivery challan")
        await _decide(client, docs[_ref(4)], "no_action")
        # 0005 (Orbit, CGST+SGST) is left undecided.

        sections = await self._generate(session, org_id)

        assert sections.itc.available.igst_minor == accepted.igst_minor
        assert sections.itc.available.cgst_minor == 0 and sections.itc.available.sgst_minor == 0
        assert sections.itc.net_eligible.igst_minor == accepted.igst_minor
        # The rejected, held, un-actioned and undecided documents carry tax the return does not.
        others = sum(docs[_ref(n)].igst_minor for n in (2, 3, 4))
        assert others > 0
        assert sections.itc.available.total_minor == accepted.igst_minor
        # And no purchase document was touched to make that so.
        async with session.begin():
            purchases_after = sorted(
                (p.id, p.taxable_minor, p.total_minor)
                for p in (await session.execute(select(PurchaseDocument))).scalars().all()
            )
        assert purchases_after == purchases_before

    @pytest.mark.parametrize("later", ["reject", "pending_action", "no_action"])
    async def test_leaving_accepted_takes_the_credit_back_out(
        self, later, client, session, org_id
    ) -> None:
        docs = await _queue(client, session, org_id, with_liability=True)
        doc = docs[_ref(1)]
        await _decide(client, doc, "accept")
        await _decide(client, doc, later, "changed our mind")

        sections = await self._generate(session, org_id)

        # The accrual and its exact reversal net to nothing in Table 4.
        assert sections.itc.net_eligible.total_minor == 0
        assert sections.itc.reversed_other.igst_minor == doc.igst_minor

    async def test_a_document_accepted_again_counts_once(self, client, session, org_id) -> None:
        docs = await _queue(client, session, org_id, with_liability=True)
        doc = docs[_ref(1)]
        for action in ("accept", "reject", "accept"):
            await _decide(client, doc, action)
        sections = await self._generate(session, org_id)
        assert sections.itc.net_eligible.igst_minor == doc.igst_minor

    async def test_a_failed_or_conflicting_push_does_not_change_credit(
        self, client, session, org_id, monkeypatch
    ) -> None:
        """Credit follows the platform's own decision, not GSTN's acknowledgement: a push that
        failed or conflicted is a flag for a person, never an input to the ledger."""
        docs = await _queue(client, session, org_id, with_liability=True)
        doc = docs[_ref(1)]
        await _decide(client, doc, "accept")
        assert (await _ask(client)).status_code == 202
        portal = StubImsClient()
        portal.simulate_external_action(PERIOD, _ref(1), "rejected")
        await _send(session, org_id, portal, monkeypatch)
        assert (await _push_of(session, org_id, _ref(1))).status == "conflict"

        sections = await self._generate(session, org_id)

        assert sections.itc.net_eligible.igst_minor == doc.igst_minor


# --------------------------------------------------------------------- bulk decisions


class TestBulkDecisions:
    def _items(self, docs, spec: list[tuple[int, str, str | None]]) -> list[dict[str, Any]]:
        return [
            {"document_id": str(docs[_ref(n)].id), "action": action, "note": note}
            for n, action, note in spec
        ]

    async def test_each_item_is_its_own_decision_with_its_own_reason_audit_and_push(
        self, client, session, org_id
    ) -> None:
        docs = await _queue(client, session, org_id)
        res = await client.post(
            DECISIONS,
            json={
                "items": self._items(
                    docs,
                    [
                        (1, "accept", "Matches PO 4471"),
                        (2, "reject", "Supplier unknown to us"),
                        (3, "pending_action", "Challan not yet received"),
                        (5, "no_action", None),
                    ],
                )
            },
        )
        assert res.status_code == 201, res.text
        results = {r["document_id"]: r["decision"] for r in res.json()["results"]}
        assert len(results) == 4
        assert results[str(docs[_ref(2)].id)]["note"] == "Supplier unknown to us"
        assert all(d["version"] == 1 and d["push"]["status"] == "queued" for d in results.values())

        async with session.begin():
            decisions = list((await session.execute(select(ImsDecision))).scalars().all())
        assert {d.note for d in decisions} == {
            "Matches PO 4471",
            "Supplier unknown to us",
            "Challan not yet received",
            None,
        }
        assert len(await _audits(session, org_id, "ims.decided")) == 4
        assert len(await _pushes(session, org_id, current_only=True)) == 4
        # Accepting posted credit exactly as a lone accept does.
        async with session.begin():
            ledger = list((await session.execute(select(ItcLedgerEntry))).scalars().all())
        assert [(e.kind, e.tax_head, e.amount_minor) for e in ledger] == [
            ("accrual", "igst", docs[_ref(1)].igst_minor)
        ]
        # A decision is a fact of who decided: the caller, not anything the body said.
        assert {d.decided_by for d in decisions} == {str(d.decided_by) for d in decisions}
        assert len({d.decided_by for d in decisions}) == 1

    async def test_it_is_all_or_nothing(self, client, session, org_id) -> None:
        docs = await _queue(client, session, org_id)
        items = self._items(docs, [(1, "accept", None), (2, "reject", "no")])
        items.append({"document_id": str(uuid.uuid4()), "action": "accept", "note": None})

        res = await client.post(DECISIONS, json={"items": items})

        assert res.status_code == 404
        async with session.begin():
            assert (await session.execute(select(ImsDecision))).scalars().all() == []
            assert (await session.execute(select(ItcLedgerEntry))).scalars().all() == []
        assert await _pushes(session, org_id) == []
        assert await _audits(session, org_id, "ims.decided") == []

    async def test_another_tenants_document_is_a_404_and_writes_nothing(
        self, client, session, org_id
    ) -> None:
        docs = await _queue(client, session, org_id)
        with _as(principal_for(uuid.uuid4(), [f"{p}:ORG" for p in FINANCE_PERMISSIONS])):
            res = await client.post(
                DECISIONS, json={"items": self._items(docs, [(1, "accept", None)])}
            )
        assert res.status_code == 404
        assert "supplier documents" in res.json()["detail"]
        async with session.begin():
            assert (await session.execute(select(ImsDecision))).scalars().all() == []

    async def test_a_document_named_twice_is_refused(self, client, session, org_id) -> None:
        docs = await _queue(client, session, org_id)
        res = await client.post(
            DECISIONS,
            json={"items": self._items(docs, [(1, "accept", None), (1, "reject", "twice")])},
        )
        assert res.status_code == 422

    async def test_the_batch_is_bounded(self, client, session, org_id) -> None:
        docs = await _queue(client, session, org_id)
        item = self._items(docs, [(1, "accept", None)])[0]
        assert (await client.post(DECISIONS, json={"items": []})).status_code == 422
        too_many = [{**item, "document_id": str(uuid.uuid4())} for _ in range(101)]
        assert (await client.post(DECISIONS, json={"items": too_many})).status_code == 422

    async def test_unknown_fields_and_unknown_actions_are_refused(
        self, client, session, org_id
    ) -> None:
        docs = await _queue(client, session, org_id)
        item = self._items(docs, [(1, "accept", None)])[0]
        assert (
            await client.post(DECISIONS, json={"items": [{**item, "approved": True}]})
        ).status_code == 422
        assert (
            await client.post(DECISIONS, json={"items": [item], "dry_run": True})
        ).status_code == 422
        assert (
            await client.post(DECISIONS, json={"items": [{**item, "action": "approve"}]})
        ).status_code == 422
        async with session.begin():
            assert (await session.execute(select(ImsDecision))).scalars().all() == []

    async def test_the_reads_do_not_grow_with_the_number_of_documents(
        self, client, session, org_id
    ) -> None:
        """No query per document: a bulk request runs the same number of SELECTs for two documents
        as for five. (Writes grow — each decision is its own rows — reads must not.)"""
        docs = await _queue(client, session, org_id)
        engine = session.get_bind()
        counts: list[int] = []

        def _count(_conn, _cursor, statement, *_args):  # type: ignore[no-untyped-def]
            if statement.lstrip().upper().startswith("SELECT"):
                counts[-1] += 1

        event.listen(engine, "before_cursor_execute", _count)
        try:
            for spec in (
                [(1, "accept", None), (2, "reject", "a")],
                [(3, "accept", None), (4, "reject", "b"), (5, "accept", None)],
            ):
                counts.append(0)
                res = await client.post(DECISIONS, json={"items": self._items(docs, spec)})
                assert res.status_code == 201, res.text
        finally:
            event.remove(engine, "before_cursor_execute", _count)
        # The response's own reads are per request, not per document; two documents vs three must
        # differ by nothing.
        assert counts[0] == counts[1], counts


# --------------------------------------------------------------------- authorization


class TestAuthorization:
    async def _ready(self, client, session, org_id) -> dict[str, ImsDocument]:
        docs = await _queue(client, session, org_id)
        await _decide(client, docs[_ref(1)], "accept")
        return docs

    async def test_a_reader_cannot_send_decisions(self, client, session, org_id) -> None:
        await self._ready(client, session, org_id)
        with _as(principal_for(org_id, ["finance.compliance.ims.read:ORG"])):
            res = await _ask(client)
        assert res.status_code == 403
        assert await _events(session, org_id, services.EVT_IMS_PUSH_REQUESTED) == []

    async def test_deciding_does_not_carry_the_right_to_send(self, client, session, org_id) -> None:
        """A clerk may record decisions for a supervisor to review; holding that alone must not let
        them leave the building."""
        await self._ready(client, session, org_id)
        with _as(principal_for(org_id, ["finance.compliance.ims.decide:ORG"])):
            res = await _ask(client)
        assert res.status_code == 403
        push = await _push_of(session, org_id, _ref(1))
        assert push.requested_at is None

    async def test_pushing_does_not_carry_the_right_to_decide(
        self, client, session, org_id
    ) -> None:
        docs = await _queue(client, session, org_id)
        doc_id = docs[_ref(1)].id
        with _as(principal_for(org_id, ["finance.compliance.ims.push:ORG"])):
            single = await client.post(f"{QUEUE}/{doc_id}/decisions", json={"action": "accept"})
            bulk = await client.post(
                DECISIONS, json={"items": [{"document_id": str(doc_id), "action": "accept"}]}
            )
        assert single.status_code == 403 and bulk.status_code == 403
        async with session.begin():
            assert (await session.execute(select(ImsDecision))).scalars().all() == []

    async def test_bulk_needs_the_decide_grant_at_org(self, client, org_id) -> None:
        body = {"items": [{"document_id": str(uuid.uuid4()), "action": "accept"}]}
        for perms in (
            [],
            ["finance.compliance.ims.read:ORG"],
            ["finance.compliance.ims.decide:OWN"],
        ):
            with _as(principal_for(org_id, perms)):
                assert (await client.post(DECISIONS, json=body)).status_code == 403
        with _as(principal_for(org_id, ["finance.compliance.ims.decide:ORG"])):
            # Granted, so the refusal is about the *document* (a 404), never the caller.
            assert (await client.post(DECISIONS, json=body)).status_code == 404

    async def test_an_own_scope_push_grant_is_below_the_org_floor(self, client, org_id) -> None:
        with _as(principal_for(org_id, ["finance.compliance.ims.push:OWN"])):
            assert (await _ask(client)).status_code == 403

    async def test_an_unentitled_organization_is_402(self, client, org_id) -> None:
        with _as(principal_for(org_id, ["finance.compliance.ims.push:ORG"], entitlements=[])):
            assert (await _ask(client)).status_code == 402
        with _as(principal_for(org_id, ["finance.compliance.ims.decide:ORG"], entitlements=[])):
            res = await client.post(
                DECISIONS, json={"items": [{"document_id": str(uuid.uuid4()), "action": "accept"}]}
            )
            assert res.status_code == 402

    async def test_the_push_reads_need_the_ims_read_grant(self, client, org_id) -> None:
        for path in (PUSHES, f"{SUMMARY}?period={PERIOD}"):
            with _as(principal_for(org_id, [])):
                assert (await client.get(path)).status_code == 403
            with _as(principal_for(org_id, ["finance.compliance.ims.push:ORG"])):
                # Sending is not seeing: the queue read is its own grant.
                assert (await client.get(path)).status_code == 403
            with _as(principal_for(org_id, ["finance.compliance.ims.read:ORG"])):
                assert (await client.get(path)).status_code == 200

    async def test_another_tenants_pushes_are_invisible(self, client, session, org_id) -> None:
        await self._ready(client, session, org_id)
        with _as(principal_for(uuid.uuid4(), [f"{p}:ORG" for p in FINANCE_PERMISSIONS])):
            listed = await client.get(PUSHES)
            summary = await client.get(f"{SUMMARY}?period={PERIOD}")
            asked = await _ask(client)
        assert listed.json()["total"] == 0
        assert summary.json()["queued"] == 0 and summary.json()["pushable"] == 0
        # The other organization has no registration of its own, so its request is refused — and
        # nothing of A's was marked on the way.
        assert asked.status_code == 422
        assert (await _push_of(session, org_id, _ref(1))).requested_at is None

    async def test_naming_another_tenants_document_is_a_404(self, client, session, org_id) -> None:
        docs = await self._ready(client, session, org_id)
        with _as(principal_for(uuid.uuid4(), [f"{p}:ORG" for p in FINANCE_PERMISSIONS])):
            res = await _ask(client, document_ids=[str(docs[_ref(1)].id)])
        assert res.status_code == 404
        assert (await _push_of(session, org_id, _ref(1))).requested_at is None


# --------------------------------------------------------------------- asking for a push


class TestAskingForAPush:
    async def _decided(self, client, session, org_id, refs=(1, 2)) -> dict[str, ImsDocument]:
        docs = await _queue(client, session, org_id)
        for n in refs:
            await _decide(client, docs[_ref(n)], "accept" if n == 1 else "reject", "reason")
        return docs

    async def test_a_request_marks_the_decisions_asked_for_and_stages_the_command(
        self, client, session, org_id
    ) -> None:
        await self._decided(client, session, org_id)
        res = await _ask(client)
        assert res.status_code == 202, res.text
        assert res.json() == {"period": PERIOD, "requested": 2, "not_pushable": 0}

        pushes = await _pushes(session, org_id, current_only=True)
        assert {p.status for p in pushes} == {"queued"}
        assert all(p.requested_at is not None and p.requested_by for p in pushes)
        events = await _events(session, org_id, services.EVT_IMS_PUSH_REQUESTED)
        assert len(events) == 1
        assert events[0].payload["period"] == PERIOD and events[0].payload["requested"] == 2
        rows = await _audits(session, org_id, "ims.push_requested")
        assert len(rows) == 1 and rows[0].after["requested"] == 2

    async def test_the_request_path_calls_no_portal_and_claims_nothing(
        self, client, session, org_id
    ) -> None:
        """202: the request half stages and returns. The claim, the call and the record all happen
        in the worker (D3) — the route never waits on a government."""
        await self._decided(client, session, org_id)
        assert (await _ask(client)).status_code == 202
        pushes = await _pushes(session, org_id, current_only=True)
        assert {p.status for p in pushes} == {"queued"} and {p.attempts for p in pushes} == {0}
        assert all(p.lease_until is None for p in pushes)

    async def test_it_can_be_limited_to_named_documents(self, client, session, org_id) -> None:
        docs = await self._decided(client, session, org_id)
        res = await _ask(client, document_ids=[str(docs[_ref(1)].id)])
        assert res.json()["requested"] == 1
        one = await _push_of(session, org_id, _ref(1))
        two = await _push_of(session, org_id, _ref(2))
        assert one.requested_at is not None and two.requested_at is None

    async def test_nothing_waiting_is_a_quiet_no_op(self, client, session, org_id) -> None:
        await _queue(client, session, org_id)
        res = await _ask(client)
        assert res.status_code == 202 and res.json()["requested"] == 0
        assert await _events(session, org_id, services.EVT_IMS_PUSH_REQUESTED) == []

    async def test_a_second_press_within_the_window_is_rate_limited(
        self, client, session, org_id
    ) -> None:
        await self._decided(client, session, org_id)
        assert (await _ask(client)).status_code == 202
        assert (await _ask(client)).status_code == 429
        assert len(await _events(session, org_id, services.EVT_IMS_PUSH_REQUESTED)) == 1

    async def test_a_malformed_period_is_refused_before_it_can_become_a_rate_limit_key(
        self, client, session, org_id
    ) -> None:
        for bad in ("2026-13", "August", "2026-8", ""):
            assert (await _ask(client, period=bad)).status_code == 422

    async def test_no_registration_means_nothing_to_record_decisions_against(
        self, client, session, org_id
    ) -> None:
        res = await _ask(client)
        assert res.status_code == 422 and "GST registration" in res.json()["detail"]

    async def test_documents_of_another_period_cannot_ride_along(
        self, client, session, org_id
    ) -> None:
        docs = await self._decided(client, session, org_id)
        assert (await _sync(client, org_id, "2026-07")).status_code == 200
        async with session.begin():
            july = [
                d
                for d in await selectors.all_ims_documents(session, org_id)
                if d.period == "2026-07"
            ]
        res = await _ask(client, document_ids=[str(docs[_ref(1)].id), str(july[0].id)])
        assert res.status_code == 422 and "one return period at a time" in res.json()["detail"]

    async def test_decisions_recorded_before_pushes_existed_are_backfilled_on_request(
        self, client, session, org_id, admin_session
    ) -> None:
        docs = await self._decided(client, session, org_id)
        # Simulate the ledger as it was before this feature: decisions, and no push rows.
        async with admin_session.begin():
            await admin_session.execute(text("TRUNCATE finance.ims_decision_pushes"))
        summary = (await client.get(f"{SUMMARY}?period={PERIOD}")).json()
        assert summary["unqueued"] == 2 and summary["pushable"] == 2

        res = await _ask(client)

        assert res.json()["requested"] == 2
        pushes = await _pushes(session, org_id, current_only=True)
        assert {(p.source_ref, p.version, p.status) for p in pushes} == {
            (_ref(1), 1, "queued"),
            (_ref(2), 1, "queued"),
        }
        assert (await _audits(session, org_id, "ims.push_requested"))[0].after["backfilled"] == 2
        assert docs  # the documents themselves were never touched

    async def test_a_failed_push_can_be_sent_again_with_a_fresh_budget(
        self, client, session, org_id, monkeypatch
    ) -> None:
        docs = await self._decided(client, session, org_id, refs=(1,))
        await _ask(client)
        refused = CannedIms(
            {_ref(1): ImsPushResult(_ref(1), "rejected", code="IMS201", message="unknown row")}
        )
        await _send(session, org_id, refused, monkeypatch)
        failed = await _push_of(session, org_id, _ref(1))
        assert failed.status == "failed" and failed.attempts == 1

        res = await client.post(PUSHES, json={"period": PERIOD})
        # (Rate limited: the same person asked seconds ago — wait it out the way a person would.)
        assert res.status_code == 429
        from platform_core import cache  # the fake Redis window is per test

        cache._redis.store.clear()  # type: ignore[union-attr]
        res = await client.post(PUSHES, json={"period": PERIOD})
        assert res.status_code == 202 and res.json()["requested"] == 1
        again = await _push_of(session, org_id, _ref(1))
        assert again.status == "queued" and again.attempts == 0 and again.error_code is None
        assert docs

    async def test_pushed_and_conflicting_decisions_are_left_alone(
        self, client, session, org_id, monkeypatch
    ) -> None:
        await self._decided(client, session, org_id)
        await _ask(client)
        portal = StubImsClient()
        portal.simulate_external_action(PERIOD, _ref(2), "accepted")
        await _send(session, org_id, portal, monkeypatch)
        assert (await _push_of(session, org_id, _ref(1))).status == "pushed"
        assert (await _push_of(session, org_id, _ref(2))).status == "conflict"

        from platform_core import cache

        cache._redis.store.clear()  # type: ignore[union-attr]
        res = await _ask(client)
        assert res.json() == {"period": PERIOD, "requested": 0, "not_pushable": 2}


# --------------------------------------------------------------------- claim, call, record


class TestSendingDecisions:
    async def _asked(self, client, session, org_id, spec=((1, "accept"), (2, "reject"))):
        docs = await _queue(client, session, org_id)
        for n, action in spec:
            await _decide(client, docs[_ref(n)], action, f"reason {n}")
        assert (await _ask(client)).status_code == 202
        return docs

    async def test_the_worker_sends_what_was_asked_and_records_what_the_portal_took(
        self, client, session, org_id, monkeypatch
    ) -> None:
        await self._asked(client, session, org_id)
        portal = StubImsClient()

        await _send(session, org_id, portal, monkeypatch)

        pushes = await _pushes(session, org_id, current_only=True)
        assert {p.status for p in pushes} == {"pushed"}
        assert all(
            p.attempts == 1 and p.pushed_at is not None and p.portal == "stub-ims" for p in pushes
        )
        assert all(p.lease_until is None and p.error_code is None for p in pushes)
        assert all(p.observed_action == domain.ims_portal_action(p.action) for p in pushes)
        # One call, one batch, under the (period, document, version) keys.
        assert len(portal.push_calls) == 1
        assert sorted(portal.push_calls[0][1]) == sorted(
            domain.ims_push_key(PERIOD, ref, 1) for ref in (_ref(1), _ref(2))
        )
        assert len(await _audits(session, org_id, "ims.pushed")) == 2
        completed = await _events(session, org_id, services.EVT_IMS_PUSH_COMPLETED)
        assert len(completed) == 1 and completed[0].payload["pushed"] == 2
        # The decisions were never touched by being sent.
        async with session.begin():
            assert len((await session.execute(select(ImsDecision))).scalars().all()) == 2

    async def test_a_redelivered_request_does_not_send_twice(
        self, client, session, org_id, monkeypatch
    ) -> None:
        await self._asked(client, session, org_id)
        portal = StubImsClient()
        monkeypatch.setattr("app.consumers.get_ims_client", lambda: portal)
        request = (await _events(session, org_id, services.EVT_IMS_PUSH_REQUESTED))[-1]

        await push_requested_ims(_envelope(request))
        await push_requested_ims(_envelope(request))

        assert len(portal.push_calls) == 1
        async with session.begin():
            assert await session.get(ProcessedEvent, request.id) is not None

    async def test_nothing_is_sent_that_nobody_asked_for(
        self, client, session, org_id, monkeypatch
    ) -> None:
        """Recording queues; only a request sends. The sweep — however long it waits — never
        sends a decision a person with the push permission has not asked for."""
        docs = await _queue(client, session, org_id)
        await _decide(client, docs[_ref(1)], "accept")
        portal = StubImsClient()

        report = await _sweep(portal).tick(now=datetime.now(UTC) + timedelta(days=30))

        assert portal.push_calls == [] and report.failed == 0
        assert (await _push_of(session, org_id, _ref(1))).status == "queued"

    async def test_a_period_is_sent_in_batches_within_the_portals_limit(
        self, client, session, org_id, monkeypatch
    ) -> None:
        docs = await self._asked(
            client,
            session,
            org_id,
            spec=[(n, "accept" if n % 2 else "reject") for n in range(1, 6)],
        )
        assert docs
        portal = StubImsClient()
        portal.push_batch_limit = 2

        await _send(session, org_id, portal, monkeypatch)

        assert [len(keys) for _, keys in portal.push_calls] == [2, 2, 1]
        assert {p.status for p in await _pushes(session, org_id, current_only=True)} == {"pushed"}

    async def test_the_route_side_holds_no_transaction_while_the_portal_is_called(
        self, client, session, org_id, monkeypatch
    ) -> None:
        await self._asked(client, session, org_id)
        seen: list[bool] = []

        class _Spy(StubImsClient):
            def push_ims_decisions(self, *, gstin: str, period: str, decisions: Any):  # type: ignore[no-untyped-def]
                seen.append(session.in_transaction())
                return super().push_ims_decisions(gstin=gstin, period=period, decisions=decisions)

        await _send(session, org_id, _Spy(), monkeypatch)

        assert seen == [False], "the IMS portal was called while a transaction was open"

    async def test_every_word_the_portal_says_is_masked_before_it_is_stored(
        self, client, session, org_id, monkeypatch
    ) -> None:
        await self._asked(client, session, org_id, spec=[(1, "accept")])
        portal = CannedIms(
            {
                _ref(1): ImsPushResult(
                    _ref(1),
                    "rejected",
                    code="IMS204",
                    message="supplier 24AABCM1234F1Z3 rejected; call 9876543210",
                )
            }
        )
        await _send(session, org_id, portal, monkeypatch)

        push = await _push_of(session, org_id, _ref(1))
        assert push.status == "failed" and push.error_code == "IMS204"
        assert "AABCM1234F" not in (push.error or "") and "9876543210" not in (push.error or "")
        assert "24**********1Z3" in (push.error or "")
        # The event carries the reference and the code, never the portal's words.
        completed = (await _events(session, org_id, services.EVT_IMS_PUSH_COMPLETED))[0]
        assert completed.payload["needs_attention"] == [
            {"source_ref": _ref(1), "status": "failed", "error_code": "IMS204"}
        ]


class TestARefusalIsNotRetried:
    async def _asked(self, client, session, org_id) -> None:
        docs = await _queue(client, session, org_id)
        await _decide(client, docs[_ref(1)], "accept")
        await _decide(client, docs[_ref(2)], "reject")
        assert (await _ask(client)).status_code == 202

    async def test_a_per_document_refusal_fails_that_decision_only(
        self, client, session, org_id, monkeypatch
    ) -> None:
        await self._asked(client, session, org_id)
        portal = CannedIms(
            {_ref(2): ImsPushResult(_ref(2), "rejected", code="IMS201", message="no such row")}
        )
        await _send(session, org_id, portal, monkeypatch)

        assert (await _push_of(session, org_id, _ref(1))).status == "pushed"
        refused = await _push_of(session, org_id, _ref(2))
        assert refused.status == "failed" and refused.error_code == "IMS201"
        # Never retried: the sweep, however late, does not call again.
        calls = len(portal.push_calls)
        await _sweep(portal).tick(now=datetime.now(UTC) + LATER)
        assert len(portal.push_calls) == calls
        assert len(await _audits(session, org_id, "ims.push_failed")) == 1

    async def test_a_whole_request_refusal_fails_every_decision_in_the_batch(
        self, client, session, org_id, monkeypatch
    ) -> None:
        await self._asked(client, session, org_id)
        portal = ScriptedIms(ImsPushRejected("IMS150", "GSTR-3B is already filed for this period"))
        await _send(session, org_id, portal, monkeypatch)

        pushes = await _pushes(session, org_id, current_only=True)
        assert {p.status for p in pushes} == {"failed"} and {p.error_code for p in pushes} == {
            "IMS150"
        }
        calls = len(portal.push_calls)
        await _sweep(portal).tick(now=datetime.now(UTC) + LATER)
        assert len(portal.push_calls) == calls

    async def test_a_conflict_is_recorded_as_one_and_only_a_new_decision_resolves_it(
        self, client, session, org_id, monkeypatch
    ) -> None:
        await self._asked(client, session, org_id)
        portal = StubImsClient()
        # Somebody acted on the row directly: the portal holds a newer decision than ours.
        portal.simulate_external_action(PERIOD, _ref(1), "rejected")
        await _send(session, org_id, portal, monkeypatch)

        conflict = await _push_of(session, org_id, _ref(1))
        assert conflict.status == "conflict" and conflict.error_code == "IMS301"
        assert conflict.observed_action == "rejected"
        assert (await _push_of(session, org_id, _ref(2))).status == "pushed"
        assert len(await _audits(session, org_id, "ims.push_conflict")) == 1

    async def test_an_already_recorded_answer_for_a_different_action_is_a_conflict(
        self, client, session, org_id, monkeypatch
    ) -> None:
        """ "Already recorded" is success only when what the portal holds is what we sent."""
        await self._asked(client, session, org_id)
        portal = CannedIms(
            {_ref(1): ImsPushResult(_ref(1), "already_recorded", recorded_action="rejected")}
        )
        await _send(session, org_id, portal, monkeypatch)
        push = await _push_of(session, org_id, _ref(1))
        assert push.status == "conflict" and push.observed_action == "rejected"


class TestAnOutageIsRetriedOnABoundedBackoff:
    async def _asked(self, client, session, org_id) -> None:
        docs = await _queue(client, session, org_id)
        await _decide(client, docs[_ref(1)], "accept")
        assert (await _ask(client)).status_code == 202

    async def test_an_outage_goes_back_to_queued_with_a_backoff_not_to_failed(
        self, client, session, org_id, monkeypatch
    ) -> None:
        await self._asked(client, session, org_id)
        before = datetime.now(UTC)
        await _send(
            session, org_id, ScriptedIms(down("upstream 503 for 24AABCM1234F1Z3")), monkeypatch
        )

        push = await _push_of(session, org_id, _ref(1))
        assert push.status == "queued" and push.attempts == 1
        assert push.error_code == domain.ERR_PORTAL_UNAVAILABLE and "AABCM1234F" not in (
            push.error or ""
        )
        assert push.next_attempt_at is not None
        assert before + domain.IMS_PUSH_RETRY_BASE <= push.next_attempt_at + timedelta(seconds=5)
        assert push.lease_until is None
        # Still asked for — it is owed a retry, not waiting on a person again.
        assert push.requested_at is not None
        assert len(await _audits(session, org_id, "ims.push_retry_scheduled")) == 1

    async def test_the_backoff_is_respected_by_the_sweep(
        self, client, session, org_id, monkeypatch
    ) -> None:
        await self._asked(client, session, org_id)
        portal = ScriptedIms(down())
        await _send(session, org_id, portal, monkeypatch)
        assert len(portal.push_calls) == 1

        await _sweep(portal).tick(now=datetime.now(UTC) + timedelta(seconds=20))
        assert len(portal.push_calls) == 1, "retried inside its own backoff"

        await _sweep(portal).tick(now=datetime.now(UTC) + timedelta(minutes=3))
        assert len(portal.push_calls) == 2
        push = await _push_of(session, org_id, _ref(1))
        assert push.status == "pushed" and push.attempts == 2

    async def test_a_rate_limit_waits_as_long_as_the_portal_asked(
        self, client, session, org_id, monkeypatch
    ) -> None:
        await self._asked(client, session, org_id)
        await _send(
            session,
            org_id,
            ScriptedIms(ImsRateLimited("slow down", retry_after=timedelta(minutes=12))),
            monkeypatch,
        )
        push = await _push_of(session, org_id, _ref(1))
        assert push.error_code == domain.ERR_RATE_LIMITED
        assert push.next_attempt_at is not None
        assert push.next_attempt_at >= datetime.now(UTC) + timedelta(minutes=11)

    async def test_the_budget_is_bounded_and_ends_in_a_visible_failure(
        self, client, session, org_id, monkeypatch
    ) -> None:
        await self._asked(client, session, org_id)
        portal = ScriptedIms(*[down()] * 30)
        await _send(session, org_id, portal, monkeypatch)
        at = datetime.now(UTC)
        for _ in range(domain.IMS_PUSH_MAX_ATTEMPTS + 3):
            at += domain.IMS_PUSH_RETRY_CEILING + timedelta(minutes=1)
            report = await _sweep(portal).tick(now=at)
            assert report.failed == 0

        push = await _push_of(session, org_id, _ref(1))
        assert push.status == "failed" and push.error_code == domain.ERR_RETRY_EXHAUSTED
        assert push.attempts == domain.IMS_PUSH_MAX_ATTEMPTS
        assert len(portal.push_calls) == domain.IMS_PUSH_MAX_ATTEMPTS
        # And the sweep went quiet: nothing newer than a `failed` row is discovered again.
        calls = len(portal.push_calls)
        await _sweep(portal).tick(now=at + LATER)
        assert len(portal.push_calls) == calls

    async def test_a_claim_that_died_at_the_last_attempt_becomes_a_failure_without_a_call(
        self, client, session, org_id, admin_session
    ) -> None:
        """The poisoned-step guard: a `submitting` claim whose budget is already spent is never
        driven again — it is settled as `retry_exhausted`, and nothing is sent."""
        await self._asked(client, session, org_id)
        async with admin_session.begin():
            await admin_session.execute(
                text(
                    "UPDATE finance.ims_decision_pushes SET status = 'submitting', attempts = :n, "
                    "lease_until = now() - interval '1 hour' WHERE organization_id = :o"
                ),
                {"n": domain.IMS_PUSH_MAX_ATTEMPTS, "o": org_id},
            )
        portal = StubImsClient()

        report = await _sweep(portal).tick(now=datetime.now(UTC))

        assert portal.push_calls == [] and report.failed == 0
        push = await _push_of(session, org_id, _ref(1))
        assert push.status == "failed" and push.error_code == domain.ERR_RETRY_EXHAUSTED


class TestACrashBetweenTheCallAndTheRecord:
    async def _asked(self, client, session, org_id, n: int = 2):
        docs = await _queue(client, session, org_id)
        for i in range(1, n + 1):
            await _decide(client, docs[_ref(i)], "accept" if i % 2 else "reject")
        assert (await _ask(client)).status_code == 202
        return docs

    async def _claim_and_call(self, session, org_id, portal):
        """The first two acts, and never the third — exactly what a process death between the call
        and the record leaves."""
        ctx = services.service_ctx(org_id)
        async with session_scope(organization_id=org_id) as s:
            claim = await services.claim_ims_push_batch(
                s, ctx, period=PERIOD, now=datetime.now(UTC), limit=50, portal=portal.name
            )
        assert claim.outcome == "batch"
        outcome = await services.call_ims_push_off_loop(portal, claim.gstin, PERIOD, claim.items)
        return claim, outcome

    async def test_the_sweep_finishes_it_and_already_recorded_is_the_success_it_is(
        self, client, session, org_id
    ) -> None:
        await self._asked(client, session, org_id)
        portal = StubImsClient()
        _, outcome = await self._claim_and_call(session, org_id, portal)
        assert outcome.kind == "answered"
        assert {p.status for p in await _pushes(session, org_id, current_only=True)} == {
            "submitting"
        }

        report = await _sweep(portal).tick(now=datetime.now(UTC) + LATER)

        assert report.failed == 0
        pushes = await _pushes(session, org_id, current_only=True)
        assert {p.status for p in pushes} == {"pushed"} and {p.attempts for p in pushes} == {2}
        # Sent twice under the same keys; the portal answered the repeat "already recorded".
        assert len(portal.push_calls) == 2
        assert sorted(portal.push_calls[0][1]) == sorted(portal.push_calls[1][1])
        assert len(await _audits(session, org_id, "ims.pushed")) == 2

    async def test_a_crash_after_the_portal_took_it_but_lost_the_answer_is_not_a_failure(
        self, client, session, org_id, monkeypatch
    ) -> None:
        await self._asked(client, session, org_id)
        portal = ScriptedIms("record_then_time_out")
        await _send(session, org_id, portal, monkeypatch)
        # The answer was lost: an outage, retried.
        assert {p.status for p in await _pushes(session, org_id, current_only=True)} == {"queued"}

        await _sweep(portal).tick(now=datetime.now(UTC) + LATER)

        pushes = await _pushes(session, org_id, current_only=True)
        assert {p.status for p in pushes} == {"pushed"}
        assert len(portal.push_calls) == 2 and sorted(portal.push_calls[0][1]) == sorted(
            portal.push_calls[1][1]
        )

    async def test_two_recorders_of_one_claim_write_one_outcome(
        self, client, session, org_id
    ) -> None:
        await self._asked(client, session, org_id)
        portal = StubImsClient()
        claim, outcome = await self._claim_and_call(session, org_id, portal)
        ctx = services.service_ctx(org_id)
        results = []
        for _ in range(2):
            async with session_scope(organization_id=org_id) as s:
                results.append(
                    await services.record_ims_push_batch(
                        s, ctx, claim, outcome, portal=portal.name, now=datetime.now(UTC)
                    )
                )
        assert (results[0].pushed, results[0].skipped) == (2, 0)
        assert (results[1].pushed, results[1].skipped) == (0, 2)
        assert len(await _audits(session, org_id, "ims.pushed")) == 2

    async def test_a_late_recorder_whose_lease_lapsed_cannot_overwrite_the_worker_that_took_over(
        self, client, session, org_id
    ) -> None:
        await self._asked(client, session, org_id, n=1)
        portal = StubImsClient()
        first, _ = await self._claim_and_call(session, org_id, portal)
        # The lease lapses; the sweep takes over and records `pushed` (attempt 2).
        await _sweep(portal).tick(now=datetime.now(UTC) + LATER)
        assert (await _push_of(session, org_id, _ref(1))).status == "pushed"
        # The first worker finally wakes up and tries to record *its* answer — a failure.
        stale = services._ImsPushOutcome(kind="rejected", error_code="IMS999", error="late")
        ctx = services.service_ctx(org_id)
        async with session_scope(organization_id=org_id) as s:
            late = await services.record_ims_push_batch(
                s, ctx, first, stale, portal=portal.name, now=datetime.now(UTC)
            )
        assert late.skipped == 1 and late.failed == 0
        after = await _push_of(session, org_id, _ref(1))
        assert after.status == "pushed" and after.error_code is None and after.attempts == 2


class TestOneCallInFlightPerDocument:
    async def test_a_newer_decision_is_not_sent_while_an_older_one_is_in_flight(
        self, client, session, org_id
    ) -> None:
        docs = await _queue(client, session, org_id)
        doc = docs[_ref(1)]
        await _decide(client, doc, "accept")
        assert (await _ask(client)).status_code == 202
        portal = StubImsClient()
        ctx = services.service_ctx(org_id)
        now = datetime.now(UTC)
        async with session_scope(organization_id=org_id) as s:
            first = await services.claim_ims_push_batch(
                s, ctx, period=PERIOD, now=now, limit=50, portal=portal.name
            )
        assert first.outcome == "batch"
        # A change of mind lands while the first is in flight, and is asked for.
        await _decide(client, doc, "reject", "changed our mind")
        from platform_core import cache

        cache._redis.store.clear()  # type: ignore[union-attr]
        assert (await _ask(client)).status_code == 202
        async with session_scope(organization_id=org_id) as s:
            second = await services.claim_ims_push_batch(
                s, ctx, period=PERIOD, now=now + timedelta(seconds=1), limit=50, portal=portal.name
            )
        assert second.outcome == "empty", "the newer decision was sent past an older one in flight"

    async def test_an_older_claim_that_lapsed_is_superseded_and_never_sent_again(
        self, client, session, org_id
    ) -> None:
        docs = await _queue(client, session, org_id)
        doc = docs[_ref(1)]
        await _decide(client, doc, "accept")
        assert (await _ask(client)).status_code == 202
        portal = StubImsClient()
        ctx = services.service_ctx(org_id)
        now = datetime.now(UTC)
        async with session_scope(organization_id=org_id) as s:
            await services.claim_ims_push_batch(
                s, ctx, period=PERIOD, now=now, limit=50, portal=portal.name
            )
        await _decide(client, doc, "reject", "changed our mind")
        from platform_core import cache

        cache._redis.store.clear()  # type: ignore[union-attr]
        assert (await _ask(client)).status_code == 202

        report = await _sweep(portal).tick(now=now + LATER)

        assert report.failed == 0
        pushes = [p for p in await _pushes(session, org_id) if p.source_ref == _ref(1)]
        assert [(p.version, p.status) for p in pushes] == [(1, "superseded"), (2, "pushed")]
        # Only the newest decision ever reached the portal.
        assert [keys for _, keys in portal.push_calls] == [
            [domain.ims_push_key(PERIOD, _ref(1), 2)]
        ]

    async def test_a_decision_superseded_before_it_was_sent_is_never_sent(
        self, client, session, org_id, monkeypatch
    ) -> None:
        docs = await _queue(client, session, org_id)
        doc = docs[_ref(1)]
        await _decide(client, doc, "accept")
        await _decide(client, doc, "reject", "changed our mind")
        assert (await _ask(client)).status_code == 202
        portal = StubImsClient()
        await _send(session, org_id, portal, monkeypatch)
        assert [keys for _, keys in portal.push_calls] == [
            [domain.ims_push_key(PERIOD, _ref(1), 2)]
        ]

    async def test_no_registration_left_fails_the_waiting_decisions_without_a_call(
        self, client, session, org_id, admin_session, monkeypatch
    ) -> None:
        docs = await _queue(client, session, org_id)
        await _decide(client, docs[_ref(1)], "accept")
        assert (await _ask(client)).status_code == 202
        async with admin_session.begin():
            await admin_session.execute(
                text(
                    "UPDATE finance.gst_registrations SET deleted_at = now() "
                    "WHERE organization_id = :o"
                ),
                {"o": org_id},
            )
        portal = StubImsClient()
        await _send(session, org_id, portal, monkeypatch)
        push = await _push_of(session, org_id, _ref(1))
        assert push.status == "failed" and push.error_code == domain.ERR_NO_REGISTRATION
        assert portal.push_calls == []


# --------------------------------------------------------------------- envelope and tenancy


class TestTheConsumerTrustsTheEnvelope:
    async def test_a_payload_naming_another_organization_is_refused(
        self, client, session, org_id, monkeypatch
    ) -> None:
        docs = await _queue(client, session, org_id)
        await _decide(client, docs[_ref(1)], "accept")
        await _ask(client)
        portal = StubImsClient()
        monkeypatch.setattr("app.consumers.get_ims_client", lambda: portal)
        request = (await _events(session, org_id, services.EVT_IMS_PUSH_REQUESTED))[-1]
        envelope = _envelope(request)
        envelope["payload"] = {**envelope["payload"], "organization_id": str(uuid.uuid4())}

        await push_requested_ims(envelope)  # must not raise

        assert portal.push_calls == []
        assert (await _push_of(session, org_id, _ref(1))).status == "queued"

    async def test_a_request_for_another_tenants_period_sends_nothing_of_ours(
        self, client, session, org_id, monkeypatch
    ) -> None:
        docs = await _queue(client, session, org_id)
        await _decide(client, docs[_ref(1)], "accept")
        await _ask(client)
        portal = StubImsClient()
        monkeypatch.setattr("app.consumers.get_ims_client", lambda: portal)
        other = uuid.uuid4()
        request = (await _events(session, org_id, services.EVT_IMS_PUSH_REQUESTED))[-1]
        envelope = {
            **_envelope(request),
            "event_id": str(uuid.uuid4()),
            "organization_id": str(other),
            "payload": {**request.payload, "organization_id": str(other)},
        }

        await push_requested_ims(envelope)

        assert portal.push_calls == []
        assert (await _push_of(session, org_id, _ref(1))).requested_at is not None
        assert (await _push_of(session, org_id, _ref(1))).status == "queued"


# --------------------------------------------------------------------- drift


class TestReconciliationAfterAPush:
    async def _pushed(self, client, session, org_id, monkeypatch, portal, refs=(1, 2)):
        docs = await _queue(client, session, org_id)
        for n in refs:
            await _decide(client, docs[_ref(n)], "accept" if n == 1 else "reject", "reason")
        assert (await _ask(client)).status_code == 202
        await _send(session, org_id, portal, monkeypatch)
        return docs

    async def _resync(self, client, org_id, portal) -> Any:
        app.dependency_overrides[get_ims_client] = lambda: portal
        try:
            return await _sync(client, org_id)
        finally:
            app.dependency_overrides.pop(get_ims_client, None)

    async def test_the_next_sync_reflects_the_decision(
        self, client, session, org_id, monkeypatch
    ) -> None:
        portal = StubImsClient()
        await self._pushed(client, session, org_id, monkeypatch, portal)

        res = await self._resync(client, org_id, portal)

        assert res.status_code == 200 and res.json()["drift_detected"] == 0
        pushes = await _pushes(session, org_id, current_only=True)
        assert all(p.drift is False and p.observed_at is not None for p in pushes)
        assert {p.observed_action for p in pushes} == {"accepted", "rejected"}
        assert await _events(session, org_id, services.EVT_IMS_PUSH_DRIFT_DETECTED) == []

    async def test_somebody_acting_on_the_portal_directly_is_flagged_once(
        self, client, session, org_id, monkeypatch
    ) -> None:
        portal = StubImsClient()
        await self._pushed(client, session, org_id, monkeypatch, portal)
        portal.simulate_external_action(PERIOD, _ref(1), "rejected")

        res = await self._resync(client, org_id, portal)

        assert res.json()["drift_detected"] == 1
        drifted = await _push_of(session, org_id, _ref(1))
        assert drifted.drift is True and drifted.drift_detected_at is not None
        assert drifted.observed_action == "rejected" and drifted.status == "pushed"
        events = await _events(session, org_id, services.EVT_IMS_PUSH_DRIFT_DETECTED)
        assert len(events) == 1
        assert events[0].payload["items"] == [
            {"source_ref": _ref(1), "pushed": "accepted", "observed": "rejected"}
        ]
        assert len(await _audits(session, org_id, "ims.push_drift")) == 1
        # The untouched decision is not drift.
        assert (await _push_of(session, org_id, _ref(2))).drift is False

        # A drift that persists is not announced again.
        again = await self._resync(client, org_id, portal)
        assert again.json()["drift_detected"] == 0
        assert len(await _events(session, org_id, services.EVT_IMS_PUSH_DRIFT_DETECTED)) == 1

    async def test_it_clears_when_the_portal_agrees_again(
        self, client, session, org_id, monkeypatch
    ) -> None:
        portal = StubImsClient()
        await self._pushed(client, session, org_id, monkeypatch, portal)
        portal.simulate_external_action(PERIOD, _ref(1), "rejected")
        await self._resync(client, org_id, portal)
        portal.simulate_external_action(PERIOD, _ref(1), "accepted")

        res = await self._resync(client, org_id, portal)

        assert res.json()["drift_cleared"] == 1
        assert (await _push_of(session, org_id, _ref(1))).drift is False
        assert len(await _audits(session, org_id, "ims.push_drift_cleared")) == 1

    async def test_a_decision_not_yet_sent_is_never_called_drift(
        self, client, session, org_id, monkeypatch
    ) -> None:
        docs = await _queue(client, session, org_id)
        await _decide(client, docs[_ref(1)], "accept")  # queued, never asked for
        portal = StubImsClient()
        portal.simulate_external_action(PERIOD, _ref(1), "rejected")

        res = await self._resync(client, org_id, portal)

        assert res.json()["drift_detected"] == 0
        assert (await _push_of(session, org_id, _ref(1))).drift is False

    async def test_a_portal_that_does_not_report_an_action_is_silence_not_disagreement(
        self, client, session, org_id, monkeypatch
    ) -> None:
        portal = StubImsClient()
        await self._pushed(client, session, org_id, monkeypatch, portal)

        class _Silent(StubImsClient):
            def fetch(self, *, gstin: str, period: str):  # type: ignore[no-untyped-def]
                return [
                    replace(doc, action=None) for doc in super().fetch(gstin=gstin, period=period)
                ]

        res = await self._resync(client, org_id, _Silent())
        assert res.json()["drift_detected"] == 0

    async def test_the_queue_can_be_filtered_to_what_drifted_and_what_was_pushed(
        self, client, session, org_id, monkeypatch
    ) -> None:
        portal = StubImsClient()
        await self._pushed(client, session, org_id, monkeypatch, portal)
        portal.simulate_external_action(PERIOD, _ref(1), "rejected")
        await self._resync(client, org_id, portal)

        drifted = (await client.get(QUEUE, params={"drift": "true"})).json()
        assert [i["source_ref"] for i in drifted["items"]] == [_ref(1)]
        assert drifted["items"][0]["push"]["drift"] is True
        pushed = (await client.get(QUEUE, params={"push_status": "pushed"})).json()
        assert sorted(i["source_ref"] for i in pushed["items"]) == [_ref(1), _ref(2)]
        listed = (await client.get(PUSHES, params={"drift": "true"})).json()
        assert [i["push"]["drift"] for i in listed["items"]] == [True]
        summary = (await client.get(f"{SUMMARY}?period={PERIOD}")).json()
        assert summary["pushed"] == 2 and summary["drift"] == 1 and summary["pushable"] == 0


# --------------------------------------------------------------------- the read model


class TestTheReadModel:
    async def test_the_detail_shows_each_decisions_own_push(
        self, client, session, org_id, monkeypatch
    ) -> None:
        docs = await _queue(client, session, org_id)
        doc = docs[_ref(1)]
        await _decide(client, doc, "accept")
        assert (await _ask(client)).status_code == 202
        await _send(session, org_id, StubImsClient(), monkeypatch)
        await _decide(client, doc, "reject", "changed our mind")

        detail = (await client.get(f"{QUEUE}/{doc.id}")).json()

        newest, oldest = detail["decisions"]
        assert (newest["version"], newest["push"]["status"]) == (2, "queued")
        assert (oldest["version"], oldest["push"]["status"]) == (1, "pushed")
        assert detail["document"]["push"]["version"] == 2

    async def test_the_push_list_is_paged_and_scoped_to_current_pushes(
        self, client, session, org_id
    ) -> None:
        docs = await _queue(client, session, org_id)
        for n in range(1, 6):
            await _decide(client, docs[_ref(n)], "accept")
        await _decide(client, docs[_ref(1)], "reject", "changed our mind")
        res = await client.get(PUSHES, params={"size": 3, "page": 1})
        body = res.json()
        assert body["total"] == 5 and len(body["items"]) == 3 and body["pages"] == 2
        assert all(i["push"]["is_current"] for i in body["items"])
        by_status = (await client.get(PUSHES, params={"status": "queued", "period": PERIOD})).json()
        assert by_status["total"] == 5

    async def test_the_summary_counts_what_a_push_would_send(self, client, session, org_id) -> None:
        docs = await _queue(client, session, org_id)
        await _decide(client, docs[_ref(1)], "accept")
        await _decide(client, docs[_ref(2)], "reject", "no")
        summary = (await client.get(f"{SUMMARY}?period={PERIOD}")).json()
        assert summary == {
            "period": PERIOD,
            "queued": 2,
            "submitting": 0,
            "pushed": 0,
            "failed": 0,
            "conflict": 0,
            "drift": 0,
            "pushable": 2,
            "unqueued": 0,
        }
        assert (await client.get(f"{SUMMARY}?period=2026-13")).status_code == 422


# --------------------------------------------------------------------- the database guard


class TestTheDatabaseKeepsThePushAStateMachine:
    async def _push(self, client, session, org_id) -> ImsPush:
        docs = await _queue(client, session, org_id)
        await _decide(client, docs[_ref(1)], "accept")
        return await _push_of(session, org_id, _ref(1))

    async def _exec(self, session, sql: str, **params: Any) -> None:
        async with session.begin():
            await session.execute(text(sql), params)

    @pytest.mark.parametrize(
        "assignment",
        [
            "period = '2026-09'",
            "source_ref = 'x'",
            "action = 'reject'",
            "version = 9",
            "idempotency_key = 'other'",
            "decision_id = gen_random_uuid()",
            "ims_document_id = gen_random_uuid()",
            "created_at = now() - interval '1 day'",
            "deleted_at = now()",
        ],
    )
    async def test_a_pushs_identity_can_never_change(
        self, assignment, client, session, org_id
    ) -> None:
        push = await self._push(client, session, org_id)
        with pytest.raises(DBAPIError, match=r"immutable|foreign key|violates"):
            await self._exec(
                session,
                # `assignment` comes from the parametrize list above, never from input.
                f"UPDATE finance.ims_decision_pushes SET {assignment} WHERE id = :id",  # noqa: S608
                id=push.id,
            )

    async def test_a_row_is_never_deleted(self, client, session, org_id) -> None:
        push = await self._push(client, session, org_id)
        with pytest.raises(DBAPIError, match="never deleted"):
            await self._exec(
                session, "DELETE FROM finance.ims_decision_pushes WHERE id = :id", id=push.id
            )

    @pytest.mark.parametrize(
        ("frm", "to"),
        [
            ("queued", "pushed"),
            ("pushed", "queued"),
            ("pushed", "failed"),
            ("conflict", "queued"),
            ("superseded", "queued"),
            ("failed", "pushed"),
            ("failed", "submitting"),
        ],
    )
    async def test_an_illegal_transition_is_refused(
        self, frm, to, client, session, org_id, admin_session
    ) -> None:
        push = await self._push(client, session, org_id)
        if frm != "queued":
            # Get the row to `frm` along legal edges, as the admin role (which owns the table and
            # is not exempt from the trigger).
            path = {
                "pushed": ["submitting", "pushed"],
                "failed": ["submitting", "failed"],
                "conflict": ["submitting", "conflict"],
                "superseded": ["superseded"],
            }[frm]
            for step in path:
                async with admin_session.begin():
                    await admin_session.execute(
                        text("UPDATE finance.ims_decision_pushes SET status = :s WHERE id = :id"),
                        {"s": step, "id": push.id},
                    )
        with pytest.raises(DBAPIError, match="cannot move"):
            async with admin_session.begin():
                await admin_session.execute(
                    text("UPDATE finance.ims_decision_pushes SET status = :s WHERE id = :id"),
                    {"s": to, "id": push.id},
                )

    async def test_the_legal_edges_are_open(self, client, session, org_id) -> None:
        push = await self._push(client, session, org_id)
        for step in ("submitting", "queued", "submitting", "failed", "queued", "superseded"):
            await self._exec(
                session,
                "UPDATE finance.ims_decision_pushes SET status = :s WHERE id = :id",
                s=step,
                id=push.id,
            )

    async def test_a_second_current_push_for_a_document_is_refused(
        self, client, session, org_id
    ) -> None:
        push = await self._push(client, session, org_id)
        with pytest.raises(DBAPIError, match=r"uq_ims_pushes_org_document_current|duplicate key"):
            await self._exec(
                session,
                """
                INSERT INTO finance.ims_decision_pushes
                  (id, organization_id, ims_document_id, decision_id, period, source_ref, action,
                   version, idempotency_key, status, is_current, attempts, drift)
                SELECT gen_random_uuid(), organization_id, ims_document_id, decision_id, period,
                       source_ref, action, 2, 'k2', 'queued', true, 0, false
                FROM finance.ims_decision_pushes WHERE id = :id
                """,
                id=push.id,
            )

    async def test_the_table_is_under_row_level_security(self, client, session, org_id) -> None:
        push = await self._push(client, session, org_id)
        other = uuid.uuid4()
        async with session.begin():
            await session.execute(
                text("SELECT set_config('app.organization_id', :o, true)"), {"o": str(other)}
            )
            seen = (
                await session.execute(text("SELECT count(*) FROM finance.ims_decision_pushes"))
            ).scalar_one()
        assert seen == 0
        # And another tenant cannot write a push into this one's book (`WITH CHECK`).
        with pytest.raises(DBAPIError, match="row-level security"):
            async with session.begin():
                await session.execute(
                    text("SELECT set_config('app.organization_id', :o, true)"), {"o": str(other)}
                )
                await session.execute(
                    text(
                        """
                        INSERT INTO finance.ims_decision_pushes
                          (id, organization_id, ims_document_id, decision_id, period, source_ref,
                           action, version, idempotency_key, status, is_current, attempts, drift)
                        VALUES (gen_random_uuid(), :victim, :doc, :dec, '2026-08', 'x', 'accept',
                                7, 'x', 'queued', false, 0, false)
                        """
                    ),
                    {"victim": org_id, "doc": push.ims_document_id, "dec": push.decision_id},
                )


class TestDiscoveryIsColumnScoped:
    """The outbox role reads identifiers, a status and timestamps on the pushes — never the
    decision's action, the portal reference, the refusal's code or the portal's words."""

    @pytest.mark.parametrize(
        "column",
        ["action", "source_ref", "error", "error_code", "observed_action", "idempotency_key"],
    )
    async def test_content_columns_are_refused(self, column: str) -> None:
        async with get_outbox_sessionmaker()() as s:
            with pytest.raises(DBAPIError, match="permission denied"):
                async with s.begin():
                    await s.execute(
                        # `column` comes from the parametrize list above, never from input.
                        text(f"SELECT {column} FROM finance.ims_decision_pushes LIMIT 1")  # noqa: S608
                    )

    async def test_the_discovery_columns_are_readable_and_writes_are_refused(self) -> None:
        async with get_outbox_sessionmaker()() as s, s.begin():
            found = await selectors.due_ims_pushes(
                s, now=datetime.now(UTC), grace=domain.FILING_RECLAIM_AFTER
            )
        assert found == []
        async with get_outbox_sessionmaker()() as s:
            with pytest.raises(DBAPIError, match="permission denied"):
                async with s.begin():
                    await s.execute(text("UPDATE finance.ims_decision_pushes SET attempts = 0"))

    async def test_only_asked_for_work_is_discovered(self, client, session, org_id) -> None:
        docs = await _queue(client, session, org_id)
        await _decide(client, docs[_ref(1)], "accept")
        now = datetime.now(UTC) + LATER
        async with get_outbox_sessionmaker()() as s, s.begin():
            assert (
                await selectors.due_ims_pushes(s, now=now, grace=domain.FILING_RECLAIM_AFTER) == []
            )
        assert (await _ask(client)).status_code == 202
        async with get_outbox_sessionmaker()() as s, s.begin():
            # Asked for, and not yet past the grace the consumer that owns it is given…
            assert (
                await selectors.due_ims_pushes(
                    s, now=datetime.now(UTC), grace=domain.FILING_RECLAIM_AFTER
                )
                == []
            )
            # …but found once it has waited past it.
            assert await selectors.due_ims_pushes(
                s, now=now, grace=domain.FILING_RECLAIM_AFTER
            ) == [(org_id, PERIOD)]


# --------------------------------------------------------------------- the contracts


class TestEventPayloadsMatchTheirSchemas:
    def _validate(self, event_type: str, payload: dict[str, Any]) -> None:
        validate_event_schema(event_type, 1, payload, contracts_root=CONTRACTS)

    async def test_requested_and_completed(self, client, session, org_id, monkeypatch) -> None:
        docs = await _queue(client, session, org_id)
        await _decide(client, docs[_ref(1)], "accept")
        await _decide(client, docs[_ref(2)], "reject", "no")
        assert (await _ask(client)).status_code == 202
        request = (await _events(session, org_id, services.EVT_IMS_PUSH_REQUESTED))[0]
        self._validate(services.EVT_IMS_PUSH_REQUESTED, request.payload)
        portal = CannedIms(
            {_ref(2): ImsPushResult(_ref(2), "rejected", code="IMS201", message="x")}
        )
        await _send(session, org_id, portal, monkeypatch)
        for completed in await _events(session, org_id, services.EVT_IMS_PUSH_COMPLETED):
            self._validate(services.EVT_IMS_PUSH_COMPLETED, completed.payload)

    async def test_drift_detected(self, client, session, org_id, monkeypatch) -> None:
        docs = await _queue(client, session, org_id)
        await _decide(client, docs[_ref(1)], "accept")
        assert (await _ask(client)).status_code == 202
        portal = StubImsClient()
        await _send(session, org_id, portal, monkeypatch)
        portal.simulate_external_action(PERIOD, _ref(1), "pending")
        app.dependency_overrides[get_ims_client] = lambda: portal
        try:
            await _sync(client, org_id)
        finally:
            app.dependency_overrides.pop(get_ims_client, None)
        events = await _events(session, org_id, services.EVT_IMS_PUSH_DRIFT_DETECTED)
        assert len(events) == 1
        self._validate(services.EVT_IMS_PUSH_DRIFT_DETECTED, events[0].payload)


class TestTheStubHonoursTheContract:
    """The seam a real adapter must satisfy, exercised on the shipped stub with no service around
    it: idempotency, conflicts, per-document refusals, whole-request refusals, batch limits."""

    def _item(self, ref: str, action: str = "accepted", version: int = 1) -> ImsPushItem:
        return ImsPushItem(ref, action, version, domain.ims_push_key(PERIOD, ref, version))

    def test_the_same_key_again_is_already_recorded(self) -> None:
        portal = StubImsClient()
        first = portal.push_ims_decisions(
            gstin=SELLER, period=PERIOD, decisions=[self._item(_ref(1))]
        )
        again = portal.push_ims_decisions(
            gstin=SELLER, period=PERIOD, decisions=[self._item(_ref(1))]
        )
        assert first[0].outcome == "recorded" and again[0].outcome == "already_recorded"

    def test_a_newer_version_overwrites_and_an_older_one_conflicts(self) -> None:
        portal = StubImsClient()

        def push(item: ImsPushItem) -> ImsPushResult:
            return portal.push_ims_decisions(gstin=SELLER, period=PERIOD, decisions=[item])[0]

        assert push(self._item(_ref(1), "accepted", 1)).outcome == "recorded"
        assert push(self._item(_ref(1), "rejected", 2)).outcome == "recorded"
        stale = push(self._item(_ref(1), "accepted", 1))
        assert stale.outcome == "conflict" and stale.recorded_action == "rejected"

    def test_the_fetch_reports_what_the_portal_holds(self) -> None:
        portal = StubImsClient()
        assert {d.action for d in portal.fetch(gstin=SELLER, period=PERIOD)} == {"no_action"}
        portal.push_ims_decisions(
            gstin=SELLER, period=PERIOD, decisions=[self._item(_ref(3), "pending")]
        )
        held = {d.source_ref: d.action for d in portal.fetch(gstin=SELLER, period=PERIOD)}
        assert held[_ref(3)] == "pending" and held[_ref(1)] == "no_action"

    def test_an_unknown_document_is_a_per_document_refusal(self) -> None:
        result = StubImsClient().push_ims_decisions(
            gstin=SELLER, period=PERIOD, decisions=[self._item("2026-08/9999")]
        )[0]
        assert result.outcome == "rejected" and result.code == "IMS201"

    @pytest.mark.parametrize(
        ("period", "gstin", "n", "code"),
        [
            ("2026-13", SELLER, 1, "IMS101"),
            (PERIOD, "27AAAAA0000A1Z0", 1, "IMS103"),
            (PERIOD, SELLER, 300, "IMS102"),
        ],
    )
    def test_a_request_the_portal_will_not_take_is_refused_whole(
        self, period, gstin, n, code
    ) -> None:
        items = [self._item(_ref(1), version=v) for v in range(1, n + 1)]
        with pytest.raises(ImsPushRejected) as raised:
            StubImsClient().push_ims_decisions(gstin=gstin, period=period, decisions=items)
        assert raised.value.code == code

    def test_the_stub_opens_no_socket_to_push(self, monkeypatch) -> None:
        import socket

        def _no(*_a: Any, **_k: Any) -> Any:  # pragma: no cover - fails the test if reached
            raise AssertionError("the stub opened a socket")

        monkeypatch.setattr(socket.socket, "connect", _no)
        StubImsClient().push_ims_decisions(
            gstin=SELLER, period=PERIOD, decisions=[self._item(_ref(1))]
        )
