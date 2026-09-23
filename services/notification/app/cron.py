"""The campaign send driver. Runs as its own process (`bin/cron`).

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


class CampaignDriver:
    """Drains due campaigns, one batch at a time, one tick at a time. See the module docstring."""

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
        self._discovery_factory = discovery_factory
        self._work_scope = work_scope
        self._batch_size = batch_size
        self._lease_seconds = lease_seconds
        self._pause_retry_seconds = pause_retry_seconds
        self._poll_interval = poll_interval
        self._max_backoff_seconds = max_backoff_seconds
        self._discovery_limit = discovery_limit
        self._stop = False
        #: Consecutive provider-unavailable outcomes and the instant a campaign is worth
        #: retrying again, kept in memory only — see the module docstring for why that is safe.
        self._backoff_streak: dict[uuid.UUID, int] = {}
        self._backoff_until: dict[uuid.UUID, datetime] = {}

    def stop(self) -> None:
        self._stop = True

    async def run(self) -> None:
        log.info(
            "notification.cron.started",
            poll_interval=self._poll_interval,
            batch_size=self._batch_size,
        )
        while not self._stop:
            report = await self.tick()
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
            await asyncio.sleep(self._poll_interval)

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
    await driver.run()


if __name__ == "__main__":
    asyncio.run(main())
