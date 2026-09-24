"""The campaign send driver, and the dispatch recovery sweep. Runs as its own process
(`bin/cron`), or folded into the worker (`app/worker.py`).

**Two sweeps, one shape.** `CampaignDriver` (below, the bulk of this module) drains campaigns.
`DispatchRecovery` (the end of it) is the other half of the dispatcher: it queues a message whose
retry backoff is over and settles a claim whose dispatcher died — see its own docstring. Both
discover across tenants on the outbox role and write under a tenant binding on the app role.

**The campaign send driver.**

**Why sending moved off the shared event consumer.** `consumers.fan_out` used to both expand a
campaign's audience *and* send it, batch after batch, inside the handler for
`engage.campaign.campaign.send_requested` — on `notification.events`, the one queue every other
product's events also ride, password resets included. A campaign of any real size could run past
RabbitMQ's `consumer_timeout`, get redelivered mid-send, and block every other event on that
queue while it did. `fan_out` now only expands the audience (`services.expand_campaign`) and
returns; sending is this sweep's job, and it is not the shared consumer — a slow or stuck
campaign here costs this process alone.

**The same claim-and-drain shape as `approval`'s escalation sweep and `subscription`'s dunning
sweep**, which is the outbox relay's own shape applied a third time (ADR-0002's split):

* **discovery** (`selectors.due_campaigns`) runs on the BYPASSRLS outbox role — it asks *which
  organizations* have a campaign to work, and there is no tenant to bind while asking. As the
  NOBYPASSRLS app role it would see nothing, and every campaign in the platform would sit
  `sending` forever, looking like a healthy, idle sweep.
* **every write** (claiming a batch, sending it, recording the outcome) runs on the app role
  inside `session_scope(organization_id=...)`, under the tenant policy. The bypass buys
  discovery, never authority.

**Claim, send, record — three transactions, the dispatcher's own shape, applied to a batch
instead of one message.** `services.claim_campaign_batch` claims recipients (`queued`, or
`sending` past a stale lease) and commits; the provider call happens with nothing open; a second
short transaction (`services.record_campaign_batch`) writes what happened. A process that dies
between claim and record leaves its batch `sending` — visible, and reclaimed by the next tick
once its lease (`Settings.engage_campaign_claim_lease_seconds`) goes stale, which is what makes a
crash mid-batch resumable instead of a stall.

**Backoff on a provider outage** is kept in memory here, per campaign, deliberately not in the
database: it exists only to stop hammering a connection that just failed, and losing it on a
restart just means one extra attempt before backing off again — never a lost campaign, since the
recipients themselves are unaffected (`record_campaign_batch` releases a `TransportUnavailable`
row straight back to `queued`).

**A route that comes back is noticed on its own schedule.** A `paused` campaign (no connected
provider, no usable sender) is retried by discovery no more often than `Settings.
engage_campaign_pause_retry_seconds` after it last paused — an operator's `POST
/engage/campaigns/{id}/resume` clears that clock immediately instead of waiting.

One campaign never blocks another: `_drain` only ever works the campaign it was called for, and
an unhandled exception from one campaign's own batch is caught, logged with its own identifiers,
and counted — never allowed to kill the whole tick's remaining candidates (the same reasoning
`approval.cron.EscalationSweep._escalate` documents for the same shape of bug).
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import selectors, services
from app.config import settings
from app.transports import Receipt, TransportError, send_batch
from platform_core.db import (
    assert_app_role,
    assert_outbox_role,
    get_outbox_engine,
    get_outbox_sessionmaker,
    init_engine,
    init_outbox_engine,
    session_scope,
)
from platform_core.logging import get_logger
from platform_core.supervise import GuardedSweep, run_supervised

log = get_logger("notification.cron")

#: A tenant-bound transaction for one organization. `session_scope` in production; a test
#: substitutes its own so the driver can be exercised without a process-wide engine — the same
#: seam `approval`'s and `subscription`'s own cron modules offer.
WorkScope = Callable[[uuid.UUID], AbstractAsyncContextManager[AsyncSession]]


@dataclass
class SweepReport:
    """What one tick did. Returned so a test can assert on it and the loop can log it."""

    considered: int = 0
    batches_sent: int = 0
    completed: int = 0
    paused: int = 0
    capped: int = 0
    backed_off: int = 0
    #: A campaign whose own batch raised. Counted rather than left to propagate, for the same
    #: reason `approval.cron.SweepReport.failed` is: one campaign's bug must not stall every
    #: other campaign due this tick, or every future tick after a naive restart.
    failed: int = 0


class CampaignDriver(GuardedSweep):
    """Drains due campaigns, one batch at a time, one tick at a time. See the module docstring.

    `run()` is `GuardedSweep`'s own: the poisoned-step guard around the whole tick (a discovery
    failure — a DB restart, a deadlock — is logged and backed off exponentially rather than
    propagated) matters more here than it would alone, because `app/worker.py` shares one
    `run_supervised` group between this driver and both event consumers — an uncaught exception
    here would take those down too. `on_tick_success` below is this class's own: the pass-level
    logging `run()` used to do inline before this shared with `scheduler.app.cron.Sweep`.
    """

    def __init__(
        self,
        *,
        discovery_factory: async_sessionmaker[AsyncSession],
        work_scope: WorkScope,
        batch_size: int = 200,
        lease_seconds: int = 300,
        pause_retry_seconds: float = 900.0,
        poll_interval: float = 15.0,
        max_backoff_seconds: float = 300.0,
        discovery_limit: int = 100,
    ) -> None:
        super().__init__(poll_interval=poll_interval, max_backoff=max_backoff_seconds)
        self._discovery_factory = discovery_factory
        self._work_scope = work_scope
        self._batch_size = batch_size
        self._lease_seconds = lease_seconds
        self._pause_retry_seconds = pause_retry_seconds
        # Also the ceiling for each campaign's own in-memory provider-outage backoff
        # (`_note_unavailable`, below) — a distinct concept from the tick-level guard
        # `GuardedSweep.__init__` stored as `_max_backoff`, sharing a default only because
        # neither ever needed a different one; kept as its own name here for that reason.
        self._max_backoff_seconds = max_backoff_seconds
        self._discovery_limit = discovery_limit
        #: Consecutive provider-unavailable outcomes and the instant a campaign is worth
        #: retrying again, kept in memory only — see the module docstring for why that is safe.
        self._backoff_streak: dict[uuid.UUID, int] = {}
        self._backoff_until: dict[uuid.UUID, datetime] = {}

    @property
    def _log_name(self) -> str:
        return "notification.cron"

    async def on_tick_success(self, result: SweepReport) -> None:
        report = result
        if report.considered or report.failed:
            log.info(
                "notification.cron.pass",
                considered=report.considered,
                batches_sent=report.batches_sent,
                completed=report.completed,
                paused=report.paused,
                capped=report.capped,
                backed_off=report.backed_off,
                failed=report.failed,
            )

    async def tick(self, now: datetime | None = None) -> SweepReport:
        """One pass: discover due campaigns, then drain each under its own tenant binding.

        `now` exists **only** for tests, the same seam `approval.cron.tick` and `subscription.
        cron.tick` offer: production never passes it, so the backoff clock and the paused-retry
        window are always the wall clock.
        """
        at = now or datetime.now(UTC)
        retry_before = at - timedelta(seconds=self._pause_retry_seconds)
        async with self._discovery_factory() as session, session.begin():
            due = await selectors.due_campaigns(
                session, paused_retry_before=retry_before, limit=self._discovery_limit
            )

        report = SweepReport(considered=len(due))
        for organization_id, campaign_id in due:
            if self._in_backoff(campaign_id, at):
                report.backed_off += 1
                continue
            await self._drain(organization_id, campaign_id, report, at)
        return report

    def _in_backoff(self, campaign_id: uuid.UUID, at: datetime) -> bool:
        until = self._backoff_until.get(campaign_id)
        return until is not None and at < until

    def _note_unavailable(self, campaign_id: uuid.UUID, at: datetime) -> None:
        streak = self._backoff_streak.get(campaign_id, 0) + 1
        self._backoff_streak[campaign_id] = streak
        delay = min(self._poll_interval * (2 ** (streak - 1)), self._max_backoff_seconds)
        self._backoff_until[campaign_id] = at + timedelta(seconds=delay)

    def _clear_backoff(self, campaign_id: uuid.UUID) -> None:
        self._backoff_streak.pop(campaign_id, None)
        self._backoff_until.pop(campaign_id, None)

    async def _drain(
        self,
        organization_id: uuid.UUID,
        campaign_id: uuid.UUID,
        report: SweepReport,
        at: datetime,
    ) -> None:
        """Batches for one campaign, until it stops, is capped, is paused, hits a provider
        outage, or nothing is claimable right now. Never more than one campaign at a time, so a
        large campaign delays only its own next batch — not another organization's, and not the
        driver's next tick.

        `at` is `tick`'s own instant, threaded through rather than read fresh here, so a test
        driving `tick(now=...)` gets a backoff clock that actually respects the instant it
        passed — the same reason `approval.cron.tick`'s own `now` override exists.
        """
        try:
            while True:
                outcome = await self._one_batch(organization_id, campaign_id)
                if outcome == "sent_batch":
                    report.batches_sent += 1
                    self._clear_backoff(campaign_id)
                    continue
                if outcome == "unavailable":
                    self._note_unavailable(campaign_id, at)
                    report.backed_off += 1
                elif outcome == "paused":
                    report.paused += 1
                elif outcome == "capped":
                    report.capped += 1
                elif outcome == "done":
                    report.completed += 1
                    self._clear_backoff(campaign_id)
                return
        except Exception:
            log.exception(
                "notification.cron.campaign_failed",
                organization_id=str(organization_id),
                campaign_id=str(campaign_id),
            )
            report.failed += 1

    async def _one_batch(self, organization_id: uuid.UUID, campaign_id: uuid.UUID) -> str:
        """Claim, send, record. Returns `sent_batch` (call again), `unavailable` (back off), or
        one of `claim_campaign_batch`'s own stopping outcomes (`done`/`stopped`/`capped`/
        `paused`/`idle`)."""
        async with self._work_scope(organization_id) as session:
            outcome, batch = await services.claim_campaign_batch(
                session,
                organization_id=organization_id,
                campaign_id=campaign_id,
                batch_size=self._batch_size,
                lease_seconds=self._lease_seconds,
            )
        if outcome != "claimed" or batch is None:
            return outcome

        # One more read, immediately before any address in this batch reaches a transport: a
        # contact erased in the window between the claim above and now must not be mailed just
        # because it was not yet erased when it was claimed. See `drop_erased_from_batch`'s
        # docstring for what this does and does not close.
        async with self._work_scope(organization_id) as session:
            batch = await services.drop_erased_from_batch(
                session, organization_id=organization_id, batch=batch
            )

        # Building the real transport (a self-hosted `smtp` connection re-resolves its host,
        # `ClaimedBatch`'s own docstring explains why) happens here, after the claim
        # transaction above has already committed — never inside it.
        paired: list[tuple[uuid.UUID, Receipt | TransportError]] = []
        if batch.messages:
            wire = await services.resolve_campaign_transport(batch)
            results = await send_batch(wire, [m.outgoing for m in batch.messages])
            paired = list(zip((m.recipient_id for m in batch.messages), results, strict=True))

        async with self._work_scope(organization_id) as session:
            any_unavailable = await services.record_campaign_batch(
                session,
                organization_id=organization_id,
                campaign_id=campaign_id,
                resolved=batch.resolved,
                sent=paired,
            )
        return "unavailable" if any_unavailable else "sent_batch"


@dataclass
class RecoveryReport:
    """What one recovery tick did. Returned so a test can assert on it and the loop can log it."""

    considered: int = 0
    #: A retry whose backoff was over, published as a fresh queued event.
    requeued: int = 0
    #: A lapsed claim settled as a failed attempt; the message will be tried again.
    reclaimed: int = 0
    #: A lapsed claim on the message's last attempt: it is `failed` now.
    gave_up: int = 0
    #: Resolved or locked by a dispatcher between discovery and now. Looked at again next pass.
    skipped: int = 0
    #: A message whose own write raised. Counted rather than propagated, for the reason
    #: `CampaignDriver`'s `failed` is: one row's fault must not stall every other message.
    failed: int = 0


class DispatchRecovery(GuardedSweep):
    """The half of the dispatcher that runs on a clock.

    The dispatcher (`app/dispatcher.py`) reacts to `message.queued` events, and an event is spent
    the moment it is claimed. Two things can leave a message owed with no event coming: a
    **transient failure** (the message goes back to `pending` with `retry_at` set to the end of
    its backoff — `services.record_failure`) and a **crash between claim and record** (the message
    is left `sending` with `retry_at` as the lease on the claim). This sweep finds both by
    `retry_at` (`selectors.due_message_redrives`) and hands each to `services.redrive_message`,
    which queues the retry or settles the lapsed claim. Without it a single SMTP blip drops a
    password reset without a trace.

    Same claim-and-drain shape as `CampaignDriver`: **discovery** is cross-tenant on the outbox
    role (column-level `SELECT` on four columns of `messages` — never an address or a body);
    **every write** runs on the app role inside `session_scope(organization_id=...)` — one
    transaction per message, so one message's failure rolls back only itself. Not strictly
    ordered by fairness: oldest `retry_at` first, `limit` per pass, so a message that raises every
    time (a bug, not weather) is retried every tick and counted in `failed` — it can crowd the pass
    only if there are `limit` of them, and it is logged with its ids each time.
    """

    def __init__(
        self,
        *,
        discovery_factory: async_sessionmaker[AsyncSession],
        work_scope: WorkScope,
        max_attempts: int,
        retry_base_seconds: float,
        retry_max_seconds: float,
        requeue_lease_seconds: int,
        poll_interval: float = 15.0,
        limit: int = 100,
        max_backoff_seconds: float = 300.0,
    ) -> None:
        super().__init__(poll_interval=poll_interval, max_backoff=max_backoff_seconds)
        self._discovery_factory = discovery_factory
        self._work_scope = work_scope
        self._max_attempts = max_attempts
        self._retry_base_seconds = retry_base_seconds
        self._retry_max_seconds = retry_max_seconds
        self._requeue_lease_seconds = requeue_lease_seconds
        self._limit = limit

    @property
    def _log_name(self) -> str:
        return "notification.dispatch_recovery"

    async def on_tick_success(self, result: RecoveryReport) -> None:
        if result.considered:
            log.info(
                "notification.dispatch_recovery.pass",
                considered=result.considered,
                requeued=result.requeued,
                reclaimed=result.reclaimed,
                gave_up=result.gave_up,
                skipped=result.skipped,
                failed=result.failed,
            )

    async def tick(self, now: datetime | None = None) -> RecoveryReport:
        """One pass. `now` exists **only** for tests — the same seam `CampaignDriver.tick` has;
        production never passes it, so every `retry_at` is compared against the wall clock."""
        at = now or datetime.now(UTC)
        async with self._discovery_factory() as session, session.begin():
            due = await selectors.due_message_redrives(session, now=at, limit=self._limit)

        report = RecoveryReport(considered=len(due))
        for organization_id, message_id in due:
            try:
                async with self._work_scope(organization_id) as session:
                    outcome: services.RedriveOutcome = await services.redrive_message(
                        session,
                        organization_id=organization_id,
                        message_id=message_id,
                        max_attempts=self._max_attempts,
                        retry_base_seconds=self._retry_base_seconds,
                        retry_max_seconds=self._retry_max_seconds,
                        requeue_lease_seconds=self._requeue_lease_seconds,
                        now=at,
                    )
            except Exception as exc:
                # The type, never the text: a database error's message can carry the statement's
                # parameters, and this process handles other people's messages.
                log.error(
                    "notification.dispatch_recovery.message_failed",
                    organization_id=str(organization_id),
                    message_id=str(message_id),
                    error_type=type(exc).__name__,
                )
                report.failed += 1
                continue
            if outcome == "requeued":
                report.requeued += 1
            elif outcome == "reclaimed":
                report.reclaimed += 1
            elif outcome == "gave_up":
                report.gave_up += 1
            else:
                report.skipped += 1
        return report


async def main() -> None:
    cfg = settings()
    engine = init_engine(cfg)
    # Every write this process makes runs on the app role under a bound tenant: started on the
    # admin or outbox DSN by mistake, row-level security would be silently inert (ADR-0002).
    await assert_app_role(engine, environment=cfg.environment)
    init_outbox_engine(cfg)
    # Discovery is cross-tenant and must bypass. Without the split it runs as the app role, sees
    # no rows, and every campaign in the platform stays `sending` forever — the outbox relay's
    # own silent stall, here.
    await assert_outbox_role(get_outbox_engine(), environment=cfg.environment)

    driver = CampaignDriver(
        discovery_factory=get_outbox_sessionmaker(),
        work_scope=lambda organization_id: session_scope(organization_id=organization_id),
        batch_size=cfg.engage_campaign_batch_size,
        lease_seconds=cfg.engage_campaign_claim_lease_seconds,
        pause_retry_seconds=cfg.engage_campaign_pause_retry_seconds,
        poll_interval=cfg.engage_campaign_poll_interval_seconds,
    )
    recovery = DispatchRecovery(
        discovery_factory=get_outbox_sessionmaker(),
        work_scope=lambda organization_id: session_scope(organization_id=organization_id),
        max_attempts=cfg.dispatch_max_attempts,
        retry_base_seconds=cfg.dispatch_retry_base_seconds,
        retry_max_seconds=cfg.dispatch_retry_max_seconds,
        requeue_lease_seconds=cfg.dispatch_requeue_lease_seconds,
        poll_interval=cfg.dispatch_recovery_poll_seconds,
        limit=cfg.dispatch_recovery_batch,
    )
    await run_supervised(campaign_driver=driver.run(), dispatch_recovery=recovery.run())


if __name__ == "__main__":
    asyncio.run(main())
