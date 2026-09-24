"""The retry path under concurrency: a send that outlives its lease, an event that arrives late, a
row the sweep cannot act on.

`test_dispatch_retry.py` proves the retry loop when every step happens in order. These are the
disorderly cases, and each is one the first version of the retry got wrong:

* **A late outcome.** The lease is 600 s and a send is usually seconds, but a send *can* outlive
  it: the sweep settles the claim as interrupted and queues another attempt, and then the provider
  answers. A late success collided with the sweep's ledger row (an `IntegrityError` in the
  consumer) and the queued retry sent a **second copy**; a late failure could flip a message
  another attempt had in flight back to `pending`. Recording is now fenced by the attempt the
  dispatcher claimed.
* **A stale event.** Every queued event is an invitation to make one attempt. The sweep's requeue
  lease can publish a second event while the first is still in the queue, and each extra event
  used to claim the message at once — burning an attempt, ignoring the backoff.
* **A poisoned row.** A due message the sweep cannot act on kept its `retry_at` and was found
  first by every pass; enough of them and the sweep never reached a live message.
* **A message whose provider may have taken it** and whose answer was lost — WhatsApp has no
  idempotency key and a template is billed, so it is not retried.

Real PostgreSQL, the real dispatcher, the real sweep.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app import domain, services
from app.config import Settings, settings
from app.models import Message
from app.transports import (
    Outgoing,
    Receipt,
    TransportOutcomeUnknown,
    TransportUnavailable,
)
from platform_core.db import TENANT_GUC
from tests.dispatch_support import (
    Crash,
    Relay,
    Scripted,
    after,
    load_deliveries,
    load_events,
    load_message,
    stage_ticket,
)
from tests.helpers import CapturingLog

pytestmark = pytest.mark.integration


class _Stalled:
    """A transport whose first send blocks until released — a live dispatcher whose lease lapses
    while it waits on the provider. Later sends succeed at once."""

    name = "smtp"

    def __init__(self, *, first_fails: bool = False) -> None:
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.calls = 0
        self.attempted: list[Outgoing] = []
        self.delivered: list[Outgoing] = []
        self._first_fails = first_fails

    async def send(self, message: Outgoing) -> Receipt:
        self.calls += 1
        self.attempted.append(message)
        if self.calls == 1:
            self.entered.set()
            await self.release.wait()
            if self._first_fails:
                raise TransportUnavailable("SMTPServerDisconnected")
        self.delivered.append(message)
        return Receipt(provider=self.name, provider_message_id=f"id-{self.calls}")


async def _stall_first_send_past_its_lease(
    session: Any, org_id: uuid.UUID, recovery: Any, transport: _Stalled
) -> tuple[uuid.UUID, Relay, asyncio.Task[int]]:
    """The dispatcher claims the message and is still waiting on the provider when the sweep
    settles the claim as interrupted. Returns the still-running dispatcher."""
    message_id = await stage_ticket(session, org_id)
    relay = Relay(session, org_id, transport)  # type: ignore[arg-type]
    first = asyncio.create_task(relay.pump())
    await asyncio.wait_for(transport.entered.wait(), timeout=10)
    claimed = await load_message(session, message_id)
    assert claimed.state == domain.STATE_SENDING
    settled = await recovery.tick(now=after(claimed))
    assert settled.reclaimed == 1  # the sweep took a claim that was still alive
    return message_id, relay, first


# ---------------------------------------------------------------------------- a late outcome


async def test_a_late_success_after_the_claim_was_settled_is_recorded_and_not_sent_again(
    session, engines, org_id, recovery, monkeypatch
) -> None:
    """The provider took the message — that is what happened, whatever the row says by now. It
    becomes `sent`, and the retry the sweep would have queued finds it sent."""
    captured = CapturingLog()
    monkeypatch.setattr(services, "log", captured)
    transport = _Stalled()
    message_id, relay, first = await _stall_first_send_past_its_lease(
        session, org_id, recovery, transport
    )

    transport.release.set()
    await first  # must not raise: the ledger row for this attempt already exists

    late = await load_message(session, message_id)
    assert (late.state, late.retry_at) == (domain.STATE_SENT, None)
    assert late.provider_message_id == "id-1"
    assert len(await load_events(session, services.EVT_MESSAGE_SENT)) == 1
    assert "dispatch.late_success" in [name for name, _ in captured.calls]
    # The sweep's row stands — the ledger is append-only, and `ON CONFLICT DO NOTHING` kept it.
    assert await load_deliveries(session, message_id) == [
        (1, "failed", services.INTERRUPTED_ATTEMPT)
    ]

    # The retry never comes: nothing is due, and nothing is queued for the dispatcher.
    assert (await recovery.tick(now=datetime.now(UTC) + timedelta(days=1))).considered == 0
    await relay.pump()
    assert len(transport.delivered) == 1
    assert transport.calls == 1


async def test_a_late_failure_after_the_claim_was_settled_changes_nothing(
    session, engines, org_id, recovery, monkeypatch
) -> None:
    """The sweep settled this attempt as interrupted and the message is waiting to be retried. A
    stale failure landing on top must not raise (it would collide with the sweep's ledger row) and
    must not touch the row."""
    captured = CapturingLog()
    monkeypatch.setattr(services, "log", captured)
    transport = _Stalled(first_fails=True)
    message_id, _, first = await _stall_first_send_past_its_lease(
        session, org_id, recovery, transport
    )
    before = await load_message(session, message_id)

    transport.release.set()
    await first

    settled_row = await load_message(session, message_id)
    assert (settled_row.state, settled_row.attempts, settled_row.retry_at) == (
        domain.STATE_PENDING,
        1,
        before.retry_at,
    )
    assert settled_row.last_error == services.INTERRUPTED_ATTEMPT
    assert await load_deliveries(session, message_id) == [
        (1, "failed", services.INTERRUPTED_ATTEMPT)
    ]
    assert "dispatch.late_failure_ignored" in [name for name, _ in captured.calls]


async def test_a_stale_failure_does_not_flip_a_message_another_attempt_has_in_flight(
    session, org_id
) -> None:
    """Attempt 1 failed and was retried; attempt 2 is in flight. A late failure for attempt 1
    (a second dispatcher's, a duplicate) would put the message back in line to be sent again on
    top of the send under way."""
    from tests.test_dispatch import _stage_one

    message = await _stage_one(session, org_id)
    async with session.begin():
        await services.claim_for_send(session, organization_id=org_id, message_id=message.id)
    async with session.begin():
        await services.record_failure(
            session,
            organization_id=org_id,
            message_id=message.id,
            attempt=1,
            provider="smtp",
            error="first",
            permanent=False,
            max_attempts=6,
        )
    async with session.begin():
        await services.claim_for_send(session, organization_id=org_id, message_id=message.id)

    async with session.begin():
        await services.record_failure(
            session,
            organization_id=org_id,
            message_id=message.id,
            attempt=1,  # stale: the message is on attempt 2
            provider="smtp",
            error="stale",
            permanent=False,
            max_attempts=6,
        )

    row = await load_message(session, message.id)
    assert (row.state, row.attempts, row.last_error) == (domain.STATE_SENDING, 2, "first")
    assert [(n, o) for n, o, _ in await load_deliveries(session, message.id)] == [(1, "failed")]


async def test_a_late_success_wins_over_a_newer_claim_and_the_newer_one_then_stands_down(
    session, org_id
) -> None:
    """The provider accepted attempt 1 after attempt 2 had been claimed. The message is `sent`
    (that is true), and when attempt 2 finishes too it records nothing more: one `sent` event."""
    from tests.test_dispatch import _stage_one

    message = await _stage_one(session, org_id)
    async with session.begin():
        await services.claim_for_send(session, organization_id=org_id, message_id=message.id)
    async with session.begin():
        await services.record_failure(
            session,
            organization_id=org_id,
            message_id=message.id,
            attempt=1,
            provider="smtp",
            error="interrupted",
            permanent=False,
            max_attempts=6,
        )
    async with session.begin():
        await services.claim_for_send(session, organization_id=org_id, message_id=message.id)

    async with session.begin():  # attempt 1's provider answer, late
        await services.record_sent(
            session,
            organization_id=org_id,
            message_id=message.id,
            attempt=1,
            provider="smtp",
            provider_message_id="late",
        )
    won = await load_message(session, message.id)
    assert (won.state, won.retry_at, won.provider_message_id) == (domain.STATE_SENT, None, "late")

    async with session.begin():  # attempt 2 finishes as well
        await services.record_sent(
            session,
            organization_id=org_id,
            message_id=message.id,
            attempt=2,
            provider="smtp",
            provider_message_id="second",
        )
    assert (await load_message(session, message.id)).provider_message_id == "late"
    assert len(await load_events(session, services.EVT_MESSAGE_SENT)) == 1


async def test_a_late_success_on_a_message_already_failed_makes_it_sent(session, org_id) -> None:
    """Failed because the sweep gave up on it; then the provider's answer arrives. It was
    delivered, so it is `sent`."""
    from tests.test_dispatch import _stage_one

    message = await _stage_one(session, org_id)
    async with session.begin():
        await services.claim_for_send(session, organization_id=org_id, message_id=message.id)
    async with session.begin():
        await services.record_failure(
            session,
            organization_id=org_id,
            message_id=message.id,
            attempt=1,
            provider="smtp",
            error="refused",
            permanent=True,
            max_attempts=6,
        )
    assert (await load_message(session, message.id)).state == domain.STATE_FAILED

    async with session.begin():
        await services.record_sent(
            session,
            organization_id=org_id,
            message_id=message.id,
            attempt=1,
            provider="smtp",
            provider_message_id="late",
        )

    row = await load_message(session, message.id)
    assert (row.state, row.retry_at) == (domain.STATE_SENT, None)


async def test_recording_an_outcome_waits_for_a_row_another_transaction_holds(
    session, engines, org_id, sweep_engines
) -> None:
    """The outcome of a send must land, so recording takes the row lock **waiting its turn**. A
    lock that skipped a held row would find nothing and drop the outcome silently."""
    from tests.test_dispatch import _stage_one

    message = await _stage_one(session, org_id)
    async with session.begin():
        await services.claim_for_send(session, organization_id=org_id, message_id=message.id)
    _, work_engine = sweep_engines
    maker = async_sessionmaker(work_engine, expire_on_commit=False)

    async def bind(s: Any) -> None:
        await s.execute(
            text(f"SELECT set_config('{TENANT_GUC}', :org, true)"), {"org": str(org_id)}
        )

    holder = maker()
    async with holder.begin():
        await bind(holder)
        await holder.execute(
            text("SELECT id FROM notification.messages WHERE id = :id FOR UPDATE"),
            {"id": message.id},
        )

        async def record() -> None:
            recorder = maker()
            async with recorder.begin():
                await bind(recorder)
                await services.record_failure(
                    recorder,
                    organization_id=org_id,
                    message_id=message.id,
                    attempt=1,
                    provider="smtp",
                    error="timeout",
                    permanent=False,
                    max_attempts=6,
                )
            await recorder.close()

        task = asyncio.create_task(record())
        await asyncio.sleep(0.5)
        assert not task.done(), "the outcome did not wait for the row lock"
    await holder.close()
    await asyncio.wait_for(task, timeout=10)

    row = await load_message(session, message.id)
    assert (row.state, row.last_error) == (domain.STATE_PENDING, "timeout")


# ---------------------------------------------------------------------------- a stale event


async def test_a_stale_queued_event_does_not_bypass_the_backoff(
    session, engines, org_id, recovery
) -> None:
    """Two events for one message: a requeue whose event is slow, then a second requeue when its
    lease lapsed. The first to arrive fails; the other must not claim the message again before its
    backoff is over."""
    message_id = await stage_ticket(session, org_id)
    transport = Scripted(
        TransportUnavailable("x"), TransportUnavailable("x"), TransportUnavailable("x")
    )
    relay = Relay(session, org_id, transport)
    await relay.pump()  # attempt 1 fails
    failed = await load_message(session, message_id)

    await recovery.tick(now=after(failed))  # requeue #1
    requeued = await load_message(session, message_id)
    _, slow = await relay.queued_events()
    relay.handed_over.append(slow.id)  # the broker is slow with #1

    await recovery.tick(now=after(requeued))  # its lease lapsed: requeue #2
    assert await relay.pump() == 1  # #2 arrives first: attempt 2 fails
    backing_off = await load_message(session, message_id)
    assert (backing_off.state, backing_off.attempts) == (domain.STATE_PENDING, 2)

    await relay.redeliver(slow.id)  # #1 arrives, well before attempt 2's backoff is over

    later = await load_message(session, message_id)
    assert later.attempts == 2, "a stale event burned an attempt immediately"
    assert transport.calls == 2


async def test_a_blackholed_provider_costs_each_message_one_attempt_per_backoff_not_one_per_event(
    session, engines, org_id, recovery
) -> None:
    """The scenario that exposed it: a provider that answers nothing, several due messages, the
    sweep republishing every unclaimed message each requeue lease. Each extra event used to burn
    an attempt at once, so six attempts went in seconds."""
    ids = [await stage_ticket(session, org_id, number=f"TK-{n}") for n in range(3)]
    transport = Scripted(*[TransportUnavailable("blackhole")] * 40)
    relay = Relay(session, org_id, transport)
    await relay.pump()  # attempt 1, three messages
    assert transport.calls == 3

    due = max([(await load_message(session, i)).retry_at for i in ids])  # type: ignore[type-var]
    lease = settings().dispatch_requeue_lease_seconds
    for round_ in range(4):  # the dispatcher is stalled: the events pile up unclaimed
        await recovery.tick(now=due + timedelta(seconds=1 + round_ * (lease + 1)))
    assert len(await relay.queued_events()) > 3 * 2

    await relay.pump()  # the dispatcher comes back and gets every event

    assert transport.calls == 6, "each message gets exactly one more attempt"
    for message_id in ids:
        assert (await load_message(session, message_id)).attempts == 2


async def test_the_queued_event_carries_the_attempt_count_it_was_made_at(
    session, engines, org_id, recovery
) -> None:
    message_id = await stage_ticket(session, org_id)
    relay = Relay(session, org_id, Scripted(TransportUnavailable("x")))
    (staged,) = await relay.queued_events()
    assert staged.payload["attempts"] == 0

    await relay.pump()
    await recovery.tick(now=after(await load_message(session, message_id)))
    _, requeued = await relay.queued_events()
    assert requeued.payload["attempts"] == 1


async def test_an_event_from_before_the_count_existed_is_honoured_as_it_always_was(
    session, engines, org_id
) -> None:
    """Events already in flight when this deploys carry no count. They must still send."""
    message_id = await stage_ticket(session, org_id)
    transport = Scripted()
    relay = Relay(session, org_id, transport)
    (queued,) = await relay.queued_events()
    legacy = {k: v for k, v in queued.payload.items() if k != "attempts"}
    from app import dispatcher
    from tests.helpers import envelope_for

    await dispatcher.dispatch(
        envelope_for(org_id, queued.event_type, legacy, event_id=queued.id),
        transports={"email": transport},  # type: ignore[dict-item]
        cfg=settings(),
    )

    assert (await load_message(session, message_id)).state == domain.STATE_SENT
    assert len(transport.delivered) == 1


# ---------------------------------------------------------------------------- a stable Message-ID


async def test_the_message_id_is_stable_across_retries_and_named_for_the_sender_domain(
    session, engines, org_id, recovery
) -> None:
    """If a crash after the provider accepted the message sends it twice, both copies carry the
    id most mailboxes already fold on."""
    message_id = await stage_ticket(session, org_id)
    cfg = Settings(email_from="Platform <info@opsgrids.example>")
    transport = Scripted(TransportUnavailable("x"), None)
    relay = Relay(session, org_id, transport, cfg=cfg)
    await relay.pump()
    await recovery.tick(now=after(await load_message(session, message_id)))
    await relay.pump()

    headers = [m.headers for m in transport.attempted]
    assert len(headers) == 2
    assert headers[0] == headers[1] == {"Message-ID": f"<{message_id}@opsgrids.example>"}


# ------------------------------------------------------------------- the lease is configured


async def test_the_dispatcher_takes_the_configured_lease_not_a_default(
    session, engines, org_id
) -> None:
    message_id = await stage_ticket(session, org_id)
    cfg = Settings(dispatch_claim_lease_seconds=1234)
    relay = Relay(session, org_id, Scripted(Crash()), cfg=cfg)
    before = datetime.now(UTC)
    with pytest.raises(Crash):
        await relay.pump()

    claimed = await load_message(session, message_id)
    assert claimed.retry_at is not None
    assert 1234 - 5 <= (claimed.retry_at - before).total_seconds() <= 1234 + 30


# ---------------------------------------------------------------------------- a claim between


async def test_a_claim_taken_between_discovery_and_the_write_is_left_alone(
    session, engines, org_id, recovery, sweep_engines
) -> None:
    """The sweep found the message due; before its write took the lock a dispatcher claimed it and
    took a lease. The message is being dealt with: the write sees a lease in the future and does
    nothing — no settling of a live claim, no second event."""
    message_id = await stage_ticket(session, org_id)
    relay = Relay(session, org_id, Scripted(TransportUnavailable("x")))
    await relay.pump()
    failed = await load_message(session, message_id)
    due_at = after(failed)

    discovery_engine, work_engine = sweep_engines
    async with async_sessionmaker(discovery_engine)() as found_by, found_by.begin():
        from app import selectors

        found = await selectors.due_message_redrives(found_by, now=due_at, limit=10)
    assert found == [(org_id, message_id)]

    maker = async_sessionmaker(work_engine, expire_on_commit=False)
    async with maker() as claimer, claimer.begin():
        await claimer.execute(
            text(f"SELECT set_config('{TENANT_GUC}', :org, true)"), {"org": str(org_id)}
        )
        claimed = await services.claim_for_send(
            claimer, organization_id=org_id, message_id=message_id, now=due_at
        )
        assert claimed is not None
    async with maker() as writer, writer.begin():
        await writer.execute(
            text(f"SELECT set_config('{TENANT_GUC}', :org, true)"), {"org": str(org_id)}
        )
        outcome = await services.redrive_message(
            writer,
            organization_id=org_id,
            message_id=message_id,
            max_attempts=6,
            now=due_at,
        )

    assert outcome == "not_due"
    row = await load_message(session, message_id)
    assert (row.state, row.attempts) == (domain.STATE_SENDING, 2)
    assert len(await load_events(session, services.EVT_MESSAGE_QUEUED)) == 1


# ------------------------------------------------------------------- a staged message is owed


async def test_a_message_whose_first_event_is_lost_is_still_owed(
    session, engines, org_id, recovery
) -> None:
    """The first `message.queued` can be dead-lettered by the broker's own retries. `stage_message`
    sets `retry_at` so the sweep queues the message again instead of leaving it `pending` with
    nothing that will ever look at it."""
    message_id = await stage_ticket(session, org_id)
    staged = await load_message(session, message_id)
    assert staged.retry_at is not None
    lease = settings().dispatch_requeue_lease_seconds
    assert lease - 5 <= (staged.retry_at - staged.created_at).total_seconds() <= lease + 30

    transport = Scripted()
    relay = Relay(session, org_id, transport)
    (lost,) = await relay.queued_events()
    relay.handed_over.append(lost.id)  # dead-lettered: never delivered

    report = await recovery.tick(now=after(staged))
    assert report.requeued == 1
    assert await relay.pump() == 1
    assert (await load_message(session, message_id)).state == domain.STATE_SENT
    await relay.redeliver(lost.id)  # found in the dead-letter queue and replayed
    assert len(transport.delivered) == 1


async def test_a_suppressed_message_is_not_owed_anything(session, engines, org_id) -> None:
    from platform_core.auth import Scope
    from tests.helpers import ctx_for

    await stage_ticket(session, org_id)  # seeds the template and rule
    write = ctx_for(org_id, "notification.delivery.preference.write", Scope.ORG)
    async with session.begin():
        await services.set_preference(
            session, write, recipient="h@northwind.example.com", channel="email", suppressed=True
        )
        staged = await services.stage_message(
            session,
            organization_id=org_id,
            channel="email",
            category="operational",
            to_address="h@northwind.example.com",
            subject="x",
            body="y",
            created_by="test",
        )
    assert staged is not None
    assert (staged.state, staged.retry_at) == (domain.STATE_SUPPRESSED, None)


# ---------------------------------------------------------------------------- a poisoned row


async def test_a_soft_deleted_due_row_is_cleared_and_not_found_again(
    session, engines, org_id, recovery
) -> None:
    """The write cannot see a soft-deleted message, so it used to leave its `retry_at` alone and
    every pass found it again — ahead of every live message (oldest first), until `limit` of them
    starved the sweep."""
    message_id = await stage_ticket(session, org_id)
    relay = Relay(session, org_id, Scripted(TransportUnavailable("x")))
    await relay.pump()
    async with session.begin():
        row = (await session.execute(select(Message).where(Message.id == message_id))).scalar_one()
        row.deleted_at = row.created_at
    due = await load_message(session, message_id)

    first = await recovery.tick(now=after(due))
    second = await recovery.tick(now=after(due, 3600))

    assert (first.considered, second.considered) == (1, 0)
    assert (await load_message(session, message_id)).retry_at is None


async def test_a_message_the_sweep_keeps_failing_on_is_deferred_and_then_given_up(
    session, engines, org_id, recovery, monkeypatch
) -> None:
    """A row whose write raises is pushed out by the backoff instead of staying at the head of the
    pass, a live message behind it is still reached, and after `MAX_REDRIVE_FAILURES` passes it is
    given up on with `message.failed` and an alert."""
    captured = CapturingLog()
    monkeypatch.setattr(services, "log", captured)
    from app import cron

    cron_log = CapturingLog()
    monkeypatch.setattr(cron, "log", cron_log)

    poisoned = await stage_ticket(session, org_id, number="TK-1")
    live = await stage_ticket(session, org_id, number="TK-2")
    relay = Relay(session, org_id, Scripted(*[TransportUnavailable("x")] * 4))
    await relay.pump()
    original = services.redrive_message

    async def redrive(*args: Any, **kwargs: Any) -> Any:
        if kwargs["message_id"] == poisoned:
            raise RuntimeError("boom: h@northwind.example.com hunter2")
        return await original(*args, **kwargs)

    monkeypatch.setattr(services, "redrive_message", redrive)

    first_due = await load_message(session, poisoned)
    report = await recovery.tick(now=after(first_due))
    assert (report.considered, report.failed) == (2, 1)
    assert report.requeued == 1  # the live message behind it was reached
    pushed = await load_message(session, poisoned)
    assert pushed.retry_at is not None and pushed.retry_at > after(first_due)

    parked_report = None
    for _ in range(domain.MAX_REDRIVE_FAILURES):
        row = await load_message(session, poisoned)
        if row.state == domain.STATE_FAILED:
            break
        parked_report = await recovery.tick(now=after(row))

    final = await load_message(session, poisoned)
    assert (final.state, final.retry_at) == (domain.STATE_FAILED, None)
    assert parked_report is not None and parked_report.parked == 1
    failed_events = await load_events(session, services.EVT_MESSAGE_FAILED)
    assert [e.payload["reason"] for e in failed_events] == ["recovery_failed"]
    assert "notification.message.recovery_gave_up" in [n for n, _ in captured.calls]
    # Types, never text.
    assert "hunter2" not in cron_log.text() + captured.text()
    assert (await load_message(session, live)).state != domain.STATE_FAILED


# ---------------------------------------------------------------------------- outcome unknown


async def _stage_whatsapp(session: Any, org_id: uuid.UUID) -> uuid.UUID:
    async with session.begin():
        message = await services.stage_message(
            session,
            organization_id=org_id,
            channel="whatsapp",
            category="operational",
            to_address="+917976603446",
            subject=None,
            body="Your invoice is due",
            created_by="test",
        )
    assert message is not None
    return message.id


async def test_a_whatsapp_send_whose_outcome_is_unknown_is_failed_not_retried(
    session, engines, org_id, recovery, monkeypatch
) -> None:
    """Meta may have received it. There is no idempotency key and a template is billed, so a
    retry would be a second message: the attempt fails as `outcome_unknown`, `message.failed` goes
    out (the alerting path) and a person decides."""
    captured = CapturingLog()
    monkeypatch.setattr(services, "log", captured)
    message_id = await _stage_whatsapp(session, org_id)
    transport = Scripted(TransportOutcomeUnknown("outcome_unknown ReadTimeout"))
    relay = Relay(session, org_id, transport, channel="whatsapp")

    await relay.pump()

    row = await load_message(session, message_id)
    assert (row.state, row.attempts, row.retry_at) == (domain.STATE_FAILED, 1, None)
    assert row.last_error == "outcome_unknown ReadTimeout"
    (failed_event,) = await load_events(session, services.EVT_MESSAGE_FAILED)
    assert failed_event.payload["reason"] == "outcome_unknown"
    assert "notification.message.outcome_unknown" in [n for n, _ in captured.calls]
    assert (await recovery.tick(now=datetime.now(UTC) + timedelta(days=2))).considered == 0
    assert await relay.pump() == 0
    assert transport.calls == 1


async def test_a_whatsapp_claim_that_lapsed_is_an_unknown_outcome_not_a_retry(
    session, engines, org_id, recovery
) -> None:
    """A dispatcher that died mid-send to Meta cannot say whether Meta got it."""
    message_id = await _stage_whatsapp(session, org_id)
    transport = Scripted(Crash())
    relay = Relay(session, org_id, transport, channel="whatsapp")
    with pytest.raises(Crash):
        await relay.pump()
    stuck = await load_message(session, message_id)

    report = await recovery.tick(now=after(stuck))

    assert (report.gave_up, report.reclaimed) == (1, 0)
    row = await load_message(session, message_id)
    assert (row.state, row.retry_at) == (domain.STATE_FAILED, None)
    (failed_event,) = await load_events(session, services.EVT_MESSAGE_FAILED)
    assert failed_event.payload["reason"] == "outcome_unknown"
    assert await relay.pump() == 0


async def test_an_email_claim_that_lapsed_is_still_retried(
    session, engines, org_id, recovery
) -> None:
    """The at-least-once decision is for email. (The mutation this pins: treating every channel
    as WhatsApp.)"""
    message_id = await stage_ticket(session, org_id)
    relay = Relay(session, org_id, Scripted(Crash()))
    with pytest.raises(Crash):
        await relay.pump()

    report = await recovery.tick(now=after(await load_message(session, message_id)))

    assert (report.reclaimed, report.gave_up) == (1, 0)
    assert (await load_message(session, message_id)).state == domain.STATE_PENDING


# ---------------------------------------------------------------------------- settings and backoff


def test_the_backoff_holds_for_fractional_settings() -> None:
    """Base and cap are floats: a cap of 100.25 s over a base of 45.5 s doubles once and stops."""
    delays = [
        domain.retry_delay(n, base_seconds=45.5, max_seconds=100.25).total_seconds()
        for n in (1, 2, 3, 4)
    ]
    assert delays == [45.5, 91.0, 100.25, 100.25]


def test_a_claim_lease_shorter_than_a_full_verified_smtp_session_is_refused() -> None:
    """A verified SMTP session is a dozen timed reads plus DNS: 15 x the longest timeout, plus a
    minute. 200 s was fine under the first rule (6 x 10 + 60 = 120) and is not now."""
    with pytest.raises(ValueError, match="dispatch_claim_lease_seconds"):
        Settings(dispatch_claim_lease_seconds=200, smtp_timeout_seconds=10.0)
    assert Settings(dispatch_claim_lease_seconds=210, smtp_timeout_seconds=10.0)


# ---------------------------------------------------------------------------- transports


@pytest.mark.parametrize(
    "error",
    [
        httpx.ConnectError("no route"),
        httpx.ConnectTimeout("slow to connect"),
        httpx.PoolTimeout("no free connection"),
    ],
)
async def test_whatsapp_retries_only_what_provably_never_reached_meta(
    error: Exception, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.test_transports import _send_against

    def handler(request: httpx.Request) -> httpx.Response:
        raise error

    from app import transports

    with pytest.raises(transports.TransportUnavailable):
        await _send_against(handler, monkeypatch)


@pytest.mark.parametrize(
    "error",
    [
        httpx.ReadTimeout("no answer"),
        httpx.RemoteProtocolError("reset after the write"),
        httpx.ReadError("connection lost"),
        httpx.WriteTimeout("stalled while writing"),
    ],
)
async def test_whatsapp_does_not_retry_when_meta_may_have_received_the_request(
    error: Exception, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app import transports
    from tests.test_transports import _send_against

    def handler(request: httpx.Request) -> httpx.Response:
        raise error

    with pytest.raises(transports.TransportOutcomeUnknown) as failure:
        await _send_against(handler, monkeypatch)
    assert str(failure.value) == f"outcome_unknown {type(error).__name__}"
    assert not isinstance(failure.value, transports.TransportUnavailable)


@pytest.mark.parametrize("status", [500, 502, 503, 504])
async def test_a_whatsapp_5xx_is_an_unknown_outcome(
    status: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app import transports
    from tests.test_transports import _send_against

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, text="<html>gateway</html>")

    with pytest.raises(transports.TransportOutcomeUnknown) as failure:
        await _send_against(handler, monkeypatch)
    assert str(failure.value) == f"outcome_unknown HTTP {status}"


async def test_a_whatsapp_429_is_still_safe_to_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    from app import transports
    from tests.test_transports import _send_against

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": {"code": 130429, "type": "OAuthException"}})

    with pytest.raises(transports.TransportUnavailable):
        await _send_against(handler, monkeypatch)


@pytest.mark.parametrize("body", [b"", b"not json", b"[1, 2]", b'{"messages": "x"}'])
async def test_a_whatsapp_success_whose_body_will_not_parse_is_still_a_success(
    body: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A 2xx is Meta accepting the message. Raising on the body would be retried as a transient
    failure and send it again."""
    from tests.test_transports import _send_against

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body)

    receipt = await _send_against(handler, monkeypatch)
    assert receipt.provider_message_id is None
