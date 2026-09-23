"""The filing-reclaim sweep for finance. Runs as its own process (`bin/cron`).

**Why this exists.** Filing an e-invoice used to run the portal call inside the same transaction
as everything else: read the invoice, call the IRP, append the row, all in one `session.begin()`
(the consumer) or one request (`POST /internal/v1/einvoice/filings`). That held a pool connection
for as long as the portal took to answer — the pool-exhaustion risk D3 exists to close everywhere
in this platform — and a process that died mid-call left nothing in `einvoice_filings` at all to
say an attempt was ever made.

`app/consumers.py` and `app/api/internal.py` now run three acts instead: **claim**
(`services.claim_autofile_attempt` / `claim_manual_filing`, one short transaction, and — see
`services.stage_registration_attempt` — writes **no row** before the portal is asked, because
registering a document is itself idempotent), **call** (`services.call_irp_register`, no
session), **record** (`services.record_autofile_result` / `record_manual_filing`, a second short
transaction). A process that dies between the call and the record leaves the invoice exactly as
it looked before any attempt — no filing row at all — and this sweep is what notices and resumes
it.

**Same claim-and-drain shape as `subscription`'s dunning sweep, `payment`'s stuck-charge sweep,
and this platform's outbox relay itself** (ADR-0002):

* **discovery** (`selectors.due_filing_reclaims`) runs on the BYPASSRLS outbox role — it asks
  *which organizations* have a reportable, unfiled invoice with no filing history at all, and
  there is no tenant to bind while asking. As the NOBYPASSRLS app role it would see nothing, and
  such an invoice would sit unfiled past its reporting window, silently.
* **every write** — re-publishing `finance.compliance.invoice.filing_requested` — runs on the app
  role inside `session_scope(organization_id=...)`, under the tenant policy. The bypass buys
  discovery, never authority.

**Re-checked under the tenant binding before acting**, because discovery and the resume run in
different transactions: an invoice a manual filing raced in between the two is found to already
have a filing row (or to no longer qualify) and is left alone rather than double-triggered.

**A poisoned invoice cannot stall the sweep.** An unexpected exception resuming one invoice is
caught, logged with its own identifiers, and counted `failed`; the tick moves on to the next
invoice, the same reasoning every other sweep in this platform states for the same shape of bug.
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

log = get_logger("finance.cron")

#: A tenant-bound transaction for one organization. `session_scope` in production; a test
#: substitutes its own, the same seam every other sweep in this platform is built on.
WorkScope = Callable[[uuid.UUID], AbstractAsyncContextManager[AsyncSession]]


@dataclass
class SweepReport:
    """What one pass did. Returned so a test can assert on it and the loop can log it."""

    considered: int = 0
    resumed: int = 0
    skipped: int = 0
    failed: int = 0

    @property
    def acted(self) -> int:
        return self.resumed


class FilingReclaimSweep:
    """Drains reportable invoices the automatic filing pipeline has lost track of, one pass at a
    time. See the module docstring."""

    def __init__(
        self,
        *,
        discovery_factory: async_sessionmaker[AsyncSession],
        work_scope: WorkScope,
        reclaim_after: timedelta = domain.FILING_RECLAIM_AFTER,
        batch_size: int = 100,
        poll_interval: float = 60.0,
    ) -> None:
        self._discovery_factory = discovery_factory
        self._work_scope = work_scope
        self._reclaim_after = reclaim_after
        self._batch_size = batch_size
        self._poll_interval = poll_interval
        self._stop_event = asyncio.Event()

    def stop(self) -> None:
        self._stop_event.set()

    async def run(self) -> None:
        log.info(
            "finance.filing_reclaim.sweep.started",
            poll_interval=self._poll_interval,
            reclaim_after_seconds=self._reclaim_after.total_seconds(),
        )
        while not self._stop_event.is_set():
            report = await self.tick()
            if report.acted or report.failed:
                log.info(
                    "finance.filing_reclaim.sweep.pass",
                    considered=report.considered,
                    resumed=report.resumed,
                    skipped=report.skipped,
                    failed=report.failed,
                )
            with suppress(TimeoutError):
                await asyncio.wait_for(self._stop_event.wait(), timeout=self._poll_interval)

    async def tick(self, now: datetime | None = None) -> SweepReport:
        """One pass: discover invoices stuck before `now - reclaim_after`, then resume each under
        its own tenant binding. Two transactions per invoice is deliberate — discovery cannot
        hold a tenant binding, and the resumed publish must run under one."""
        at = now or datetime.now(UTC)
        cutoff = at - self._reclaim_after
        report = SweepReport()

        async with self._discovery_factory() as session, session.begin():
            due = await selectors.due_filing_reclaims(
                session, older_than=cutoff, limit=self._batch_size
            )

        report.considered = len(due)
        for organization_id, invoice_id in due:
            await self._resume_one(organization_id, invoice_id, at, report)
        return report

    async def _resume_one(
        self,
        organization_id: uuid.UUID,
        invoice_id: uuid.UUID,
        now: datetime,
        report: SweepReport,
    ) -> None:
        """Re-check one invoice is still stuck under the tenant binding, then ask the pipeline
        to try it again from attempt 1 — exactly the request finalizing the document would have
        staged, had it arrived. Nothing calls the portal here: this only re-arms the queue,
        because the point of the sweep is discovery, and the same claim/call/record split the
        route and the consumer already run is what should do the calling."""
        try:
            async with self._work_scope(organization_id) as session:
                ctx = services.service_ctx(organization_id)
                invoice = await selectors.get_invoice(session, organization_id, invoice_id)
                if invoice is None:
                    report.skipped += 1
                    return
                filings = await selectors.filings_for_invoice(session, organization_id, invoice.id)
                if filings:
                    # A manual filing, or the pipeline's own retry, reached it since discovery
                    # ran. The good race.
                    report.skipped += 1
                    return
                if not domain.should_auto_file(
                    supply_type=invoice.supply_type,
                    document_type=invoice.document_type,
                    status=invoice.status,
                ):
                    report.skipped += 1
                    return
                services.request_filing(
                    session, ctx, invoice, attempt=1, trigger="reclaim", now=now
                )
            report.resumed += 1
        except Exception:
            log.exception(
                "finance.filing_reclaim.sweep.resume_failed",
                organization_id=str(organization_id),
                invoice_id=str(invoice_id),
            )
            report.failed += 1


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
