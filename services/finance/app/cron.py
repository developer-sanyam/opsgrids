"""The filing-reclaim sweep for finance. Runs inside `finance-worker` (`app/worker.py`), supervised
beside the filing consumer; `bin/cron` runs it alone for local debugging.

**Why this exists.** Every compliance portal call in this service used to run inside the same
transaction as everything else: read the invoice, call the IRP (or the e-Way portal, or IMS),
append the row, all in one `session.begin()` (the consumer) or one request (`app/api/
internal.py`). That held a pool connection — and the invoice's row lock — for as long as the
portal took to answer, the pool-exhaustion risk D3 exists to close everywhere in this platform,
and a process that died mid-call left no trace of what it had asked for.

Every one of those calls is now three acts (D3, `docs/io-outside-transactions.md`): **claim**
(one short transaction), **call** (no session at all), **record** (a second short transaction).
What a crash between the call and the record leaves behind differs by act, and so does what this
sweep does about it:

* **Registration** writes *no row* before the call — registering is idempotent by document (the
  IRN is a pure function of it) — so a crash leaves the invoice with no filing row at all.
  `selectors.due_filing_reclaims` finds those, and the sweep re-arms the queue
  (`filing_requested`, attempt 1). It never calls the IRP itself for a registration.
* **A `pending` e-invoice row** (an outage) has a retry scheduled through the scheduler, whose
  table this service may not read. One that has outlived its retry's delay plus grace, with no
  `filing_requested` staged since, is taken as lost and re-queued at the next attempt number
  (`services.claim_filing_redrive`); the attempt budget bounds it.
* **A `cancelling` claim** (IRN withdrawal), and **a `submitting` / `pending` / `cancelling`
  e-Way row**, are re-driven *here*, through the same claim/call/record split the route uses
  (`claim_filing_redrive` / `claim_eway_redrive`, `call_*` with no transaction, `record_*`). The
  portal's "already cancelled" / "already generated" answers are recorded as the successes they
  are, and a generation is re-sent under the same (document, transporter/vehicle part)
  idempotency key its first claim computed.

**Same claim-and-drain shape as `subscription`'s dunning sweep, `payment`'s stuck-charge sweep,
and this platform's outbox relay itself** (ADR-0002):

* **discovery** (`selectors.due_filing_reclaims`, `due_stale_filings`, `due_stale_eway`) runs on
  the BYPASSRLS outbox role — it asks *which organizations* have something stuck, and there is no
  tenant to bind while asking. As the NOBYPASSRLS app role it would see nothing, and a document
  would sit unfiled (or a withdrawal half-done) past its statutory window, silently. The role
  holds column-scoped `SELECT` on exactly the columns those three queries read
  (`migrations/env.py`), nothing else.
* **every write** — re-publishing `filing_requested`, appending a lease row, recording what the
  portal said — runs on the app role inside `session_scope(organization_id=...)`, under the
  tenant policy. The bypass buys discovery, never authority.

**Re-checked under the tenant binding before acting**, because discovery and the resume run in
different transactions: the invoice is taken `FOR UPDATE SKIP LOCKED` (one a request or another
replica is driving right now is left alone this tick, not queued behind), and the newest row's
own deadline — `domain.resume_after(attempts)` for a claim, `domain.pending_redrive_after` for an
outage — is compared exactly. Discovery's `older_than` is only the coarse floor.

**Bounded backoff and a lease.** Every re-drive of a claim appends a *fresh* claim row, so "newest
row older than its deadline" is itself the lease: nobody else can pass it for that document until
the new row is stale too, and each deadline is longer than the last (`resume_after`).

**A poisoned step cannot stall the sweep, or loop forever.** Past `domain.MAX_COMPLIANCE_RESUMES`
drives a claim becomes a terminal `failed` row carrying `retry_exhausted` — a failure a person
sees, not a row picked up on every tick. An unexpected exception on one invoice is caught, logged
with its own identifiers, and counted `failed`; the tick moves on to the next invoice, the same
reasoning every other sweep in this platform states for the same shape of bug.
"""

from __future__ import annotations

import asyncio
import signal
import uuid
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager, suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import domain, selectors, services
from app.config import settings
from app.deps import get_eway_client, get_irp_client
from app.eway.base import EwayClient
from app.irp.base import IrpClient
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
from platform_core.supervise import GuardedSweep

log = get_logger("finance.cron")

#: A tenant-bound transaction for one organization. `session_scope` in production; a test
#: substitutes its own, the same seam every other sweep in this platform is built on.
WorkScope = Callable[[uuid.UUID], AbstractAsyncContextManager[AsyncSession]]


#: The newest-row statuses `selectors.due_stale_filings` / `due_stale_eway` look for. Registration
#: has no claim row (see `services.stage_registration_attempt`), so the e-invoice side has only
#: the outage row and the cancellation claim; the e-Way side has both claims and its outage row.
STALE_FILING_STATUSES: tuple[str, ...] = ("pending", "cancelling")
STALE_EWAY_STATUSES: tuple[str, ...] = ("submitting", "pending", "cancelling")


@dataclass
class SweepReport:
    """What one pass did. Returned so a test can assert on it and the loop can log it.

    `resumed` counts every invoice the pass moved forward — a lost registration re-armed, a
    stale `pending` re-queued, a claim re-driven through the portal; `exhausted` counts claims
    the poisoned-step guard turned terminal; `skipped` those re-checked under the tenant binding
    and found not (or no longer) due."""

    considered: int = 0
    resumed: int = 0
    exhausted: int = 0
    skipped: int = 0
    failed: int = 0

    @property
    def acted(self) -> int:
        return self.resumed + self.exhausted


class FilingReclaimSweep(GuardedSweep):
    """Drains compliance filings the platform has lost track of, one pass at a time. See the
    module docstring."""

    def __init__(
        self,
        *,
        discovery_factory: async_sessionmaker[AsyncSession],
        work_scope: WorkScope,
        irp_client_factory: Callable[[], IrpClient] = get_irp_client,
        eway_client_factory: Callable[[], EwayClient] = get_eway_client,
        reclaim_after: timedelta = domain.FILING_RECLAIM_AFTER,
        batch_size: int = 100,
        poll_interval: float = 60.0,
        max_backoff: float = 300.0,
    ) -> None:
        super().__init__(poll_interval=poll_interval, max_backoff=max_backoff)
        self._discovery_factory = discovery_factory
        self._work_scope = work_scope
        self._irp_client_factory = irp_client_factory
        self._eway_client_factory = eway_client_factory
        self._reclaim_after = reclaim_after
        self._batch_size = batch_size

    @property
    def _log_name(self) -> str:
        return "finance.filing_reclaim.sweep"

    async def on_tick_success(self, result: SweepReport) -> None:
        report = result
        if report.acted or report.failed:
            log.info(
                "finance.filing_reclaim.sweep.pass",
                considered=report.considered,
                resumed=report.resumed,
                exhausted=report.exhausted,
                skipped=report.skipped,
                failed=report.failed,
            )

    async def tick(self, now: datetime | None = None) -> SweepReport:
        """One pass: discover, then resume each invoice under its own tenant binding.

        Three discovery reads in one short transaction on the outbox role — invoices with no
        filing row at all, invoices whose newest e-invoice row is a stale `pending`/`cancelling`,
        invoices whose newest e-Way row is a stale `submitting`/`pending`/`cancelling` — each
        bounded by `batch_size`, each excluding what it cannot act on (see the selectors), and
        each only a floor: the exact per-attempt deadline is re-checked under the tenant binding
        before anything is driven.

        `now` pins the clock for a test. Left `None`, the clock is read **per invoice**, not once
        for the pass: a pass that spends minutes on portal calls must not judge its last invoice
        against the instant its first was discovered.
        """
        at = now or datetime.now(UTC)
        report = SweepReport()

        async with self._discovery_factory() as session, session.begin():
            lost = await selectors.due_filing_reclaims(
                session, older_than=at - self._reclaim_after, limit=self._batch_size
            )
            stale_filings = await selectors.due_stale_filings(
                session, floors=self._filing_floors(at), limit=self._batch_size
            )
            stale_eway = await selectors.due_stale_eway(
                session, floors=self._eway_floors(at), limit=self._batch_size
            )

        report.considered = len(lost) + len(stale_filings) + len(stale_eway)
        for organization_id, invoice_id in lost:
            await self._resume_one(organization_id, invoice_id, now or datetime.now(UTC), report)
        for organization_id, invoice_id in stale_filings:
            await self._redrive_filing(
                organization_id, invoice_id, now or datetime.now(UTC), report
            )
        for organization_id, invoice_id in stale_eway:
            await self._redrive_eway(organization_id, invoice_id, now or datetime.now(UTC), report)
        return report

    def _filing_floors(self, at: datetime) -> dict[str, datetime]:
        """A `pending` e-invoice row may have a scheduled retry up to
        `pending_redrive_after(MAX_FILING_ATTEMPTS)` out, so it is not discovered before then
        (rows inside their own retry window must not fill the batch); a `cancelling` claim is
        discovered from the first re-drive deadline on and re-checked exactly under the tenant."""
        return {
            "pending": at
            - max(
                self._reclaim_after,
                domain.pending_redrive_after(domain.MAX_FILING_ATTEMPTS),
            ),
            "cancelling": at - self._reclaim_after,
        }

    def _eway_floors(self, at: datetime) -> dict[str, datetime]:
        return dict.fromkeys(STALE_EWAY_STATUSES, at - self._reclaim_after)

    async def _resume_one(
        self,
        organization_id: uuid.UUID,
        invoice_id: uuid.UUID,
        now: datetime,
        report: SweepReport,
    ) -> None:
        """One reportable invoice with no filing row: `services.claim_lost_filing` re-checks it
        under the tenant binding and either re-queues it (at the next attempt number), leaves it
        (backing off, filed meanwhile, no registration), or ends it with a terminal
        `retry_exhausted` row. Nothing calls the portal here — the consumer's own
        claim/call/record is what files a document."""
        ctx = services.service_ctx(organization_id)
        try:
            async with self._work_scope(organization_id) as session:
                outcome = await services.claim_lost_filing(session, ctx, invoice_id, now=now)
            self._count(outcome, report)
        except Exception:
            log.exception(
                "finance.filing_reclaim.sweep.resume_failed",
                organization_id=str(organization_id),
                invoice_id=str(invoice_id),
            )
            report.failed += 1

    async def _redrive_filing(
        self,
        organization_id: uuid.UUID,
        invoice_id: uuid.UUID,
        now: datetime,
        report: SweepReport,
    ) -> None:
        """One stale e-invoice filing: claim (`services.claim_filing_redrive`, which re-checks
        the deadline, applies the poisoned-step guard and appends the lease row), then — for a
        `cancelling` claim only — call the portal with **no transaction open**, then record the
        answer in a second short transaction. A `pending` row is only re-queued, never called
        from here: the consumer's own claim/call/record is what files a document."""
        ctx = services.service_ctx(organization_id)
        try:
            async with self._work_scope(organization_id) as session:
                step = await services.claim_filing_redrive(session, ctx, invoice_id, now=now)
            if step.cancel is None:
                self._count(step.outcome, report)
                return

            outcome = await services.call_irp_cancel_off_loop(
                self._irp_client_factory(), step.cancel
            )

            async with self._work_scope(organization_id) as session:
                filing = await services.record_irn_cancellation(
                    session, ctx, step.cancel, outcome, at=step.cancel.at
                )
            log.info(
                "finance.filing_reclaim.cancel_redriven",
                organization_id=str(organization_id),
                invoice_id=str(invoice_id),
                outcome=outcome.kind,
                filing_status=filing.status,
            )
            report.resumed += 1
        except Exception:
            log.exception(
                "finance.filing_reclaim.sweep.redrive_failed",
                organization_id=str(organization_id),
                invoice_id=str(invoice_id),
            )
            report.failed += 1

    async def _redrive_eway(
        self,
        organization_id: uuid.UUID,
        invoice_id: uuid.UUID,
        now: datetime,
        report: SweepReport,
    ) -> None:
        """One stale e-Way row: claim (`services.claim_eway_redrive`), call the portal with **no
        transaction open**, record — for a generation (`submitting`, or a `pending` outage) and a
        withdrawal (`cancelling`) alike."""
        ctx = services.service_ctx(organization_id)
        try:
            client = self._eway_client_factory()
            async with self._work_scope(organization_id) as session:
                step = await services.claim_eway_redrive(
                    session, ctx, invoice_id, now=now, portal=client.name
                )

            if step.generate is not None:
                generated = await services.call_eway_generate_off_loop(
                    client, step.generate.payload
                )
                async with self._work_scope(organization_id) as session:
                    bill = await services.record_eway_generation(
                        session, ctx, step.generate, generated, portal=client.name
                    )
                outcome_kind = generated.kind
            elif step.cancel is not None:
                cancelled = await services.call_eway_cancel_off_loop(client, step.cancel)
                async with self._work_scope(organization_id) as session:
                    bill = await services.record_eway_cancellation(
                        session, ctx, step.cancel, cancelled
                    )
                outcome_kind = cancelled.kind
            else:
                self._count(step.outcome, report)
                return

            log.info(
                "finance.filing_reclaim.eway_redriven",
                organization_id=str(organization_id),
                invoice_id=str(invoice_id),
                step=step.outcome,
                outcome=outcome_kind,
                eway_status=bill.status,
            )
            report.resumed += 1
        except Exception:
            log.exception(
                "finance.filing_reclaim.sweep.redrive_failed",
                organization_id=str(organization_id),
                invoice_id=str(invoice_id),
            )
            report.failed += 1

    @staticmethod
    def _count(outcome: str, report: SweepReport) -> None:
        if outcome == "exhausted":
            report.exhausted += 1
        elif outcome == "requeued":
            report.resumed += 1
        else:
            report.skipped += 1


async def main() -> None:
    cfg = settings()
    engine = init_engine(cfg)
    # Every write this process makes runs on the app role under a bound tenant, so the same
    # assertion the consumer makes applies here too (ADR-0002).
    await assert_app_role(engine, environment=cfg.environment)
    init_outbox_engine(cfg)
    # Discovery is cross-tenant and must bypass. Without the split it runs as the app role, sees
    # no rows, and a reportable invoice the pipeline lost track of sits unfiled forever — the same
    # silent stall ADR-0002 records for the relay, in a path with a statutory reporting deadline.
    await assert_outbox_role(get_outbox_engine(), environment=cfg.environment)

    sweep = FilingReclaimSweep(
        discovery_factory=get_outbox_sessionmaker(),
        work_scope=lambda organization_id: session_scope(organization_id=organization_id),
    )

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        with suppress(NotImplementedError):
            loop.add_signal_handler(sig, sweep.stop)

    await sweep.run()


if __name__ == "__main__":
    asyncio.run(main())
