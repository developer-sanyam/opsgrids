"""A transient send failure is retried, and a dispatcher that dies mid-send is recovered.

The defect: `dispatch()` marks the queued event processed in its claim transaction, so once a send
failed the message went back to `pending` and **nothing ever queued it again** — the event had
been acked, and a redelivery of it would have stopped at `already_processed`. `dispatch_max_attempts
= 6` gave one real attempt, and one SMTP blip dropped a password reset without a trace. The old
tests called `services.record_failure` directly (`test_dispatch.py` says so), which is exactly why
nothing noticed: the missing step is the one *between* the record and the next attempt.

So these run the whole loop, on real PostgreSQL, through the real `dispatcher.dispatch` and the
real recovery sweep. Only two things are simulated, both outside this service (see
`dispatch_support.py`): the relay and broker, and the passage of time. The disorderly cases — a
late outcome, a stale event, a poisoned row — are `test_dispatch_fencing.py`.

Every test that asserts "exactly once" counts sends on the transport, not rows.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import domain, services
from app.config import Settings, settings
from app.models import Message
from app.transports import TransportRefused, TransportUnavailable
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

TICKET = "support.desk.ticket.created"


# ---------------------------------------------------------------------------- the retry


async def test_a_transient_failure_is_retried_and_delivered_exactly_once(
    session, engines, org_id, recovery
) -> None:
    """The defect end to end. One SMTP blip, and the message still arrives — once."""
    message_id = await stage_ticket(session, org_id)
    transport = Scripted(TransportUnavailable("SMTPServerDisconnected"), None)
    relay = Relay(session, org_id, transport)

    # Attempt 1 fails. The message is owed, and something must remember that.
    assert await relay.pump() == 1
    failed_once = await load_message(session, message_id)
    assert (failed_once.state, failed_once.attempts) == (domain.STATE_PENDING, 1)
    assert failed_once.last_error == "SMTPServerDisconnected"
    assert failed_once.retry_at is not None
    assert await load_deliveries(session, message_id) == [(1, "failed", "SMTPServerDisconnected")]
    # Not a terminal failure: nothing tells the rest of the platform it failed.
    assert await load_events(session, services.EVT_MESSAGE_FAILED) == []
    # And nothing has queued it yet: the backoff has to run first.
    assert await relay.pump() == 0

    # Before the backoff is over the sweep leaves it alone.
    early = await recovery.tick(now=failed_once.retry_at - timedelta(seconds=5))  # type: ignore[operator]
    assert early.considered == 0

    # After it, the sweep queues it again — as a new event, through the outbox.
    report = await recovery.tick(now=after(failed_once))
    assert (report.considered, report.requeued, report.failed) == (1, 1, 0)
    assert len(await relay.queued_events()) == 2

    # The dispatcher picks it up like any other and sends it.
    assert await relay.pump() == 1
    delivered = await load_message(session, message_id)
    assert (delivered.state, delivered.attempts) == (domain.STATE_SENT, 2)
    assert delivered.retry_at is None
    assert delivered.last_error is None
    assert await load_deliveries(session, message_id) == [
        (1, "failed", "SMTPServerDisconnected"),
        (2, "sent", None),
    ]
    assert transport.calls == 2
    assert len(transport.delivered) == 1

    # At-least-once delivery of either event, any number of times, still sends nothing more.
    for event in await relay.queued_events():
        await relay.redeliver(event.id)
    assert len(transport.delivered) == 1
    assert (await recovery.tick(now=after(failed_once, 86_400))).considered == 0


async def test_a_permanent_refusal_is_not_retried(session, engines, org_id, recovery) -> None:
    """A bad address will never work. Retrying it is just a slower way to fail."""
    message_id = await stage_ticket(session, org_id)
    transport = Scripted(TransportRefused("SMTPRecipientsRefused 550"))
    relay = Relay(session, org_id, transport)

    await relay.pump()

    refused = await load_message(session, message_id)
    assert (refused.state, refused.attempts) == (domain.STATE_FAILED, 1)
    assert refused.retry_at is None
    assert len(await load_events(session, services.EVT_MESSAGE_FAILED)) == 1
    # Nothing is due, now or ever, and nothing was queued again.
    assert (await recovery.tick(now=datetime.now(UTC) + timedelta(days=30))).considered == 0
    assert await relay.pump() == 0
    assert transport.calls == 1


async def test_attempts_are_bounded_and_the_last_failure_alerts(
    session, engines, org_id, recovery, monkeypatch
) -> None:
    """A provider that never comes back cannot loop a message for ever: after
    `dispatch_max_attempts` sends it is `failed`, `message.failed` is published once, and an
    alert is logged. Exactly that many sends, no more."""
    captured = CapturingLog()
    monkeypatch.setattr(services, "log", captured)
    max_attempts = settings().dispatch_max_attempts
    message_id = await stage_ticket(session, org_id)
    transport = Scripted(*[TransportUnavailable("SMTPServerDisconnected")] * (max_attempts + 3))
    relay = Relay(session, org_id, transport)

    await relay.pump()
    for _ in range(max_attempts * 2):
        row = await load_message(session, message_id)
        if row.state == domain.STATE_FAILED:
            break
        assert row.retry_at is not None
        await recovery.tick(now=after(row))
        await relay.pump()

    final = await load_message(session, message_id)
    assert (final.state, final.attempts) == (domain.STATE_FAILED, max_attempts)
    assert final.retry_at is None
    assert transport.calls == max_attempts
    assert [d[:2] for d in await load_deliveries(session, message_id)] == [
        (n, "failed") for n in range(1, max_attempts + 1)
    ]
    failed_events = await load_events(session, services.EVT_MESSAGE_FAILED)
    assert len(failed_events) == 1
    assert failed_events[0].payload["attempts"] == max_attempts
    alerts = [fields for name, fields in captured.calls if name == "notification.message.gave_up"]
    assert len(alerts) == 1
    assert alerts[0]["message_id"] == str(message_id)
    assert alerts[0]["organization_id"] == str(org_id)
    # The alert names ids and counts. Never the recipient.
    assert "northwind.example.com" not in captured.text()

    # Terminal means terminal: nothing queues it again.
    assert (await recovery.tick(now=datetime.now(UTC) + timedelta(days=10))).considered == 0
    assert await relay.pump() == 0
    assert transport.calls == max_attempts


def test_the_backoff_doubles_and_is_capped() -> None:
    delays = [
        domain.retry_delay(n, base_seconds=60, max_seconds=3600).total_seconds()
        for n in range(1, 9)
    ]
    assert delays == [60, 120, 240, 480, 960, 1920, 3600, 3600]
    assert domain.retry_delay(10_000, base_seconds=60, max_seconds=3600).total_seconds() == 3600


async def test_the_backoff_is_what_the_row_records(session, engines, org_id) -> None:
    message_id = await stage_ticket(session, org_id)
    relay = Relay(session, org_id, Scripted(TransportUnavailable("x")))
    before = datetime.now(UTC)
    await relay.pump()
    row = await load_message(session, message_id)
    assert row.retry_at is not None
    waited = (row.retry_at - before).total_seconds()
    assert (
        settings().dispatch_retry_base_seconds - 1
        <= waited
        <= (settings().dispatch_retry_base_seconds + 30)
    )


async def test_an_unclassified_exception_is_retried_without_its_text_being_stored(
    session, engines, org_id
) -> None:
    """An exception nobody classified is treated as an outage — but its message is not stored, on
    the row, in the ledger or on the bus: a client library's text can carry what it was doing."""
    message_id = await stage_ticket(session, org_id)
    relay = Relay(
        session,
        org_id,
        Scripted(RuntimeError("login h@northwind.example.com password hunter2 refused")),
    )

    await relay.pump()

    row = await load_message(session, message_id)
    assert (row.state, row.last_error) == (domain.STATE_PENDING, "unclassified RuntimeError")
    ledger = await load_deliveries(session, message_id)
    assert ledger == [(1, "failed", "unclassified RuntimeError")]
    assert "hunter2" not in repr(ledger)


# ---------------------------------------------------------------------------- a crash mid-send


async def test_a_crash_between_claim_and_send_recovers(session, engines, org_id, recovery) -> None:
    """The other way a message goes missing. The dispatcher claims the message (committing the
    claim and marking the event processed), then dies before it records an outcome. The row is
    `sending`, the broker redelivers the unacked event and it stops at `already_processed` — so
    before this fix that message stayed `sending` for ever, silently."""
    message_id = await stage_ticket(session, org_id)
    transport = Scripted(Crash())
    relay = Relay(session, org_id, transport)

    with pytest.raises(Crash):
        await relay.pump()

    stuck = await load_message(session, message_id)
    assert (stuck.state, stuck.attempts) == (domain.STATE_SENDING, 1)
    assert stuck.retry_at is not None  # the lease on the claim

    # The broker redelivers what was never acked: a no-op, as it always was.
    (queued,) = await relay.queued_events()
    await relay.redeliver(queued.id)
    assert transport.calls == 1
    assert (await load_message(session, message_id)).state == domain.STATE_SENDING

    # While the lease holds — the dispatcher may simply be slow — the sweep leaves it alone.
    assert (await recovery.tick(now=stuck.retry_at - timedelta(seconds=5))).considered == 0  # type: ignore[operator]

    # Once it lapses the claim is settled as a failed attempt and the message owed again.
    settled = await recovery.tick(now=after(stuck))
    assert (settled.considered, settled.reclaimed, settled.failed) == (1, 1, 0)
    reclaimed = await load_message(session, message_id)
    assert (reclaimed.state, reclaimed.attempts) == (domain.STATE_PENDING, 1)
    assert reclaimed.last_error == services.INTERRUPTED_ATTEMPT
    assert await load_deliveries(session, message_id) == [
        (1, "failed", services.INTERRUPTED_ATTEMPT)
    ]

    # Then the ordinary retry path: backoff, requeue, send.
    await recovery.tick(now=after(reclaimed))
    assert await relay.pump() == 1
    done = await load_message(session, message_id)
    assert (done.state, done.attempts) == (domain.STATE_SENT, 2)
    assert len(transport.delivered) == 1


async def test_a_crash_on_the_last_attempt_gives_up_instead_of_looping(
    session, engines, org_id, recovery, monkeypatch
) -> None:
    """A message that kills the process every time it is sent cannot loop: the claim counted the
    attempt, so a lapsed claim on the last one is `failed`, with an alert."""
    captured = CapturingLog()
    monkeypatch.setattr(services, "log", captured)
    message_id = await stage_ticket(session, org_id)
    max_attempts = settings().dispatch_max_attempts
    # Every attempt but the last fails ordinarily, through the real retry path; the last kills the
    # process.
    relay = Relay(
        session,
        org_id,
        Scripted(*[TransportUnavailable("SMTPServerDisconnected")] * (max_attempts - 1), Crash()),
    )
    await relay.pump()
    for _ in range(max_attempts - 2):
        await recovery.tick(now=after(await load_message(session, message_id)))
        await relay.pump()
    await recovery.tick(now=after(await load_message(session, message_id)))
    with pytest.raises(Crash):
        await relay.pump()
    stuck = await load_message(session, message_id)
    assert stuck.attempts == max_attempts

    report = await recovery.tick(now=after(stuck))

    assert (report.gave_up, report.reclaimed) == (1, 0)
    final = await load_message(session, message_id)
    assert (final.state, final.retry_at) == (domain.STATE_FAILED, None)
    assert len(await load_events(session, services.EVT_MESSAGE_FAILED)) == 1
    assert [n for n, _ in captured.calls].count("notification.message.gave_up") == 1


async def test_a_lost_queued_event_is_queued_again_and_still_sends_once(
    session, engines, org_id, recovery
) -> None:
    """Between "waiting" and "claimed" nothing may strand a message. If the event the sweep
    published is lost, the lease it left on the row lapses and the sweep publishes another. When
    the lost one turns up late, it finds the message already sent."""
    message_id = await stage_ticket(session, org_id)
    transport = Scripted(TransportUnavailable("x"), None)
    relay = Relay(session, org_id, transport)
    await relay.pump()
    failed = await load_message(session, message_id)

    first = await recovery.tick(now=after(failed))
    assert first.requeued == 1
    requeued = await load_message(session, message_id)
    assert requeued.retry_at is not None
    original, lost = await relay.queued_events()
    relay.handed_over.extend([original.id, lost.id])  # the broker drops `lost` on the floor

    second = await recovery.tick(now=after(requeued))
    assert (second.requeued, second.failed) == (1, 0)
    assert await relay.pump() == 1
    assert (await load_message(session, message_id)).state == domain.STATE_SENT

    await relay.redeliver(lost.id)  # found again, later
    assert len(transport.delivered) == 1
    assert transport.calls == 2


async def test_the_sweep_leaves_a_message_a_dispatcher_is_holding(
    session, engines, org_id, recovery, sweep_engines
) -> None:
    """A row another transaction holds is skipped, not waited on and not settled: that message is
    being dealt with. Looked at again next pass."""
    message_id = await stage_ticket(session, org_id)
    relay = Relay(session, org_id, Scripted(Crash()))
    with pytest.raises(Crash):
        await relay.pump()
    stuck = await load_message(session, message_id)

    _, work_engine = sweep_engines
    holder = async_sessionmaker(work_engine, expire_on_commit=False)()
    async with holder.begin():
        await holder.execute(
            text(f"SELECT set_config('{TENANT_GUC}', :org, true)"), {"org": str(org_id)}
        )
        await holder.execute(
            text("SELECT id FROM notification.messages WHERE id = :id FOR UPDATE"),
            {"id": message_id},
        )
        held = await recovery.tick(now=after(stuck))
        assert (held.considered, held.skipped, held.reclaimed) == (1, 1, 0)
    await holder.close()

    free = await recovery.tick(now=after(stuck))
    assert (free.considered, free.reclaimed) == (1, 1)


async def test_two_queued_events_for_one_message_cannot_both_claim_it(
    session, engines, org_id, sweep_engines
) -> None:
    """The claim locks the row. Two dispatchers reading `pending` at the same moment would both
    go on to send: the state check is only a guard if nobody can change the state between the
    read and the write."""
    message_id = await stage_ticket(session, org_id)
    _, work_engine = sweep_engines
    maker = async_sessionmaker(work_engine, expire_on_commit=False)

    async def claim(s: AsyncSession) -> Message | None:
        await s.execute(
            text(f"SELECT set_config('{TENANT_GUC}', :org, true)"), {"org": str(org_id)}
        )
        return await services.claim_for_send(s, organization_id=org_id, message_id=message_id)

    first, second = maker(), maker()
    async with first.begin():
        won = await claim(first)
        # `first` has not committed; `second` meets the lock while it is held.
        async with second.begin():
            # Bounded, so a claim that does not skip the held row fails here instead of hanging
            # the suite waiting on the lock.
            lost = await asyncio.wait_for(claim(second), timeout=10)
    await first.close()
    await second.close()

    assert won is not None
    assert lost is None
    assert (await load_message(session, message_id)).attempts == 1


# ---------------------------------------------------------------------------- the sweep itself


async def test_one_pass_recovers_every_organizations_messages_each_under_its_own_tenant(
    session, engines, org_id, recovery
) -> None:
    """Discovery is cross-tenant (the outbox role); every write is tenant-bound (the app role).
    Two organizations' due messages are both found by one pass, and each is written under its own
    binding — a write under the wrong one would be refused by row-level security, not made."""
    other_org = uuid.uuid4()
    mine = await stage_ticket(session, org_id)
    theirs_session_maker = None

    # A second tenant, staged through its own binding.
    from tests.test_cron import _org_bound_session

    async with _org_bound_session(other_org) as theirs_session:
        theirs_session_maker = theirs_session
        theirs = await stage_ticket(theirs_session, other_org)
        for relay in (
            Relay(session, org_id, Scripted(TransportUnavailable("x"))),
            Relay(theirs_session, other_org, Scripted(TransportUnavailable("x"))),
        ):
            await relay.pump()
        due = max(
            (await load_message(session, mine)).retry_at,  # type: ignore[type-var]
            (await load_message(theirs_session, theirs)).retry_at,  # type: ignore[type-var]
        )

        report = await recovery.tick(now=due + timedelta(seconds=1))  # type: ignore[operator]

        assert (report.considered, report.requeued, report.failed) == (2, 2, 0)
        assert (
            len(await load_events(session, services.EVT_MESSAGE_QUEUED)) == 2
        )  # 1 staged + 1 retry
        assert len(await load_events(theirs_session, services.EVT_MESSAGE_QUEUED)) == 2
    assert theirs_session_maker is not None


async def test_a_message_nobody_owes_anything_is_never_swept(
    session, engines, org_id, recovery
) -> None:
    """A `sent` message with a stale `retry_at` (it should not have one) is not even found by the
    sweep, and if a state change races the sweep between discovery and the lock, the write leaves
    it alone: nothing is queued, settled or failed — only the stray value is cleared."""
    message_id = await stage_ticket(session, org_id)
    relay = Relay(session, org_id, Scripted())
    await relay.pump()
    async with session.begin():
        row = (await session.execute(select(Message).where(Message.id == message_id))).scalar_one()
        assert row.state == domain.STATE_SENT
        row.retry_at = datetime.now(UTC) - timedelta(days=1)

    assert (await recovery.tick(now=datetime.now(UTC))).considered == 0
    assert (await load_message(session, message_id)).state == domain.STATE_SENT

    # The race: found while it was due, resolved before the write took its lock.
    async with session.begin():
        outcome = await services.redrive_message(
            session,
            organization_id=org_id,
            message_id=message_id,
            max_attempts=settings().dispatch_max_attempts,
            now=datetime.now(UTC),
        )
    assert outcome == "not_due"
    after = await load_message(session, message_id)
    assert (after.state, after.retry_at) == (domain.STATE_SENT, None)
    assert len(await load_events(session, services.EVT_MESSAGE_QUEUED)) == 1  # the original only
    assert await load_events(session, services.EVT_MESSAGE_FAILED) == []


async def test_a_message_that_opts_out_while_waiting_is_not_sent_on_retry(
    session, engines, org_id, recovery
) -> None:
    """Suppression is checked again at every claim, so a recipient who opts out during the
    backoff is not mailed by the retry."""
    from platform_core.auth import Scope
    from tests.helpers import ctx_for

    message_id = await stage_ticket(session, org_id)
    transport = Scripted(TransportUnavailable("x"), None)
    relay = Relay(session, org_id, transport)
    await relay.pump()
    failed = await load_message(session, message_id)

    async with session.begin():
        await services.set_preference(
            session,
            ctx_for(org_id, "notification.delivery.preference.write", Scope.ORG),
            recipient="h@northwind.example.com",
            channel="email",
            suppressed=True,
        )
    await recovery.tick(now=after(failed))
    await relay.pump()

    row = await load_message(session, message_id)
    assert (row.state, row.retry_at) == (domain.STATE_SUPPRESSED, None)
    assert transport.delivered == []


async def test_the_outbox_role_can_find_messages_but_not_read_them(session, sweep_engines) -> None:
    """The discovery grant is four columns. A bug in a cross-tenant query must not be able to read
    a recipient's address or the message they were sent."""
    discovery_engine, _ = sweep_engines
    async with discovery_engine.connect() as conn:
        await conn.execute(
            text("SELECT organization_id, id, state, retry_at FROM notification.messages")
        )
        await conn.rollback()
        for column in ("to_address", "body", "subject"):
            with pytest.raises(DBAPIError, match="permission denied"):
                await conn.execute(text(f"SELECT {column} FROM notification.messages"))  # noqa: S608
            await conn.rollback()


# ---------------------------------------------------------------------------- settings


def test_a_claim_lease_shorter_than_one_real_send_is_refused() -> None:
    """A lease that can lapse while a send is still running would let the sweep settle a message
    that is being sent, and it would go out twice."""
    with pytest.raises(ValueError, match="dispatch_claim_lease_seconds"):
        Settings(dispatch_claim_lease_seconds=30, smtp_timeout_seconds=10.0)
    with pytest.raises(ValueError, match="dispatch_claim_lease_seconds"):
        Settings(dispatch_claim_lease_seconds=200, smtp_timeout_seconds=60.0)
    assert Settings(dispatch_claim_lease_seconds=600).dispatch_claim_lease_seconds == 600


def test_the_retry_schedule_must_make_sense() -> None:
    with pytest.raises(ValueError, match="dispatch_retry_base_seconds"):
        Settings(dispatch_retry_base_seconds=0)
    with pytest.raises(ValueError, match="dispatch_retry_base_seconds"):
        Settings(dispatch_retry_base_seconds=600, dispatch_retry_max_seconds=60)
