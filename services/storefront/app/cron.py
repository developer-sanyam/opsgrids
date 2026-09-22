"""The custom-domain sweep for storefront (ADR-0012 §6). Runs as its own process (`bin/cron`).

**Why this exists at all, restated because getting it wrong is silent.** `bin/cron`'s scaffold
default runs `python -m app.relay`; this service's outbox is empty by design (ADR-0012 §15
publishes no event), so left at the default the sweep would start, drain nothing, report itself
healthy, and **no merchant custom domain could ever reach `verified`** — the same silent-nothing
failure shape ADR-0010 §4 names for the directory role. `bin/cron` is repointed at this module
and refuses to start at all if it is missing (`bin/cron`'s own check), which turns that failure
into a crash loop `docker compose ps` shows rather than a healthy container doing nothing.

**Same claim-and-drain shape as `services/subscription/app/cron.py`'s dunning sweep and
`services/approval/app/cron.py`'s escalation sweep, with one deliberate difference in which
bypass role discovery runs on.** Those two discover on the outbox role, because they exist to
find *which organizations have overdue work* and there is no tenant to bind while asking.
This sweep discovers on `opsgrids_storefront_directory` instead — the same BYPASSRLS,
SELECT-only reader `resolve_public_store_by_host` and `tls-authorize` already use — because
ADR-0012 §6 is explicit that the sweep must cost **no new grant on any bypass role**:

    # on opsgrids_storefront_directory — SELECT, one table, unchanged
    SELECT DISTINCT organization_id FROM storefront.storefronts WHERE custom_domain IS NOT NULL

    # then, per organization, on the ordinary app role
    async with session_scope(organization_id=org):   # <= 1 claim row per tenant
        ...probe, write, audit...

**Every write runs on the NOBYPASSRLS app role inside `session_scope(organization_id=...)`**,
one transaction per organization, under the tenant policy — the bypass buys discovery, never
authority, the same split every sweep and the relay itself makes (ADR-0002).

**No audit row per probe** (ADR-0012 §15): a tick that finds nothing changed writes nothing.
An actual state transition audits with a system actor (`acting_as="storefront-domain-sweep"`,
`actor_id=None`), and the host cache is invalidated on every one of those — a state transition
that does not clear the cache leaves a stale answer serving (or refusing) for up to
`store_host_cache_seconds`.

**"A check that cannot run never takes a domain off the air" (§6), enforced structurally.** An
unexpected exception while advancing one organization's claim is caught, logged with its
organization id (never the token), and counted as `SweepReport.failed` — the tick moves on to
the next organization rather than propagating out of `run()` and killing the whole sweep, the
same reasoning `services/approval/app/cron.py`'s `_escalate` states for its own poisoned-step
guard. **The row is left untouched on that path — no state is written from a bug.**
"""

from __future__ import annotations

import asyncio
import signal
import uuid
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager, suppress
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import domain, services
from app.config import assert_directory_role_configured, settings
from app.models import AuditLog, CustomDomainClaim, Storefront
from app.probes import ProbeSet, default_probe_set
from app.selectors import discover_organizations_with_custom_domain, get_domain_claim_for_org
from app.services import _forget_host  # the same invalidation every route uses
from platform_core.audit import record_audit
from platform_core.db import (
    assert_app_role,
    get_directory_sessionmaker,
    init_directory_engine,
    init_engine,
    session_scope,
)
from platform_core.logging import get_logger

log = get_logger("storefront.cron")

#: A tenant-bound transaction for one organization. `session_scope` in production; a test
#: substitutes its own — the same seam `subscription/app/cron.py`'s `WorkScope` documents.
WorkScope = Callable[[uuid.UUID], AbstractAsyncContextManager[AsyncSession]]

#: The system actor every sweep-driven audit row carries (ADR-0012 §15: "Sweep-driven
#: transitions carry a system actor").
SWEEP_ACTOR = "storefront-domain-sweep"


@dataclass
class SweepReport:
    """What one pass did. Returned so a test can assert on it and the loop can log it."""

    considered: int = 0
    checked: int = 0
    transitioned: int = 0
    released: int = 0
    skipped: int = 0
    failed: int = 0

    @property
    def acted(self) -> int:
        return self.checked + self.transitioned + self.released


class DomainSweep:
    """Drains the due domain-verification work, one pass at a time. See the module docstring."""

    def __init__(
        self,
        *,
        discovery_factory: async_sessionmaker[AsyncSession],
        work_scope: WorkScope,
        probes: ProbeSet | None = None,
        batch_size: int = 20,
        poll_interval: float = 60.0,
    ) -> None:
        self._discovery_factory = discovery_factory
        self._work_scope = work_scope
        self._probes = probes or default_probe_set()
        self._batch_size = batch_size
        self._poll_interval = poll_interval
        # An `Event` rather than a plain flag: `run()`'s sleep between ticks is a `wait()` on
        # this, so `stop()` interrupts it immediately instead of waiting out the rest of a
        # (possibly 60 s) poll interval before the loop condition is re-checked — the "clean
        # SIGTERM shutdown" the loop exists to have, not merely a flag nothing ever reads
        # promptly.
        self._stop_event = asyncio.Event()

    def stop(self) -> None:
        self._stop_event.set()

    async def run(self) -> None:
        log.info(
            "storefront.domain.sweep.started",
            poll_interval=self._poll_interval,
            batch_size=self._batch_size,
        )
        while not self._stop_event.is_set():
            report = await self.tick()
            if report.acted or report.failed:
                log.info(
                    "storefront.domain.sweep.pass",
                    considered=report.considered,
                    checked=report.checked,
                    transitioned=report.transitioned,
                    released=report.released,
                    skipped=report.skipped,
                    failed=report.failed,
                )
            with suppress(TimeoutError):
                await asyncio.wait_for(self._stop_event.wait(), timeout=self._poll_interval)

    async def tick(self, now: datetime | None = None) -> SweepReport:
        """One pass: discover organizations with a claimed domain, then advance each under its
        own tenant binding. `now` exists for tests; production always reads the wall clock.

        Capped at `batch_size` organizations per tick (ADR-0012 §6: "processes at most 20 due
        domains per tick, so a slow resolver cannot make the tick unbounded"). At ≤50 merchants
        platform-wide this is headroom, not a real ceiling.
        """
        at = now or datetime.now(UTC)
        async with self._discovery_factory() as session:
            org_ids = await discover_organizations_with_custom_domain(session)

        report = SweepReport(considered=len(org_ids))
        for organization_id in org_ids[: self._batch_size]:
            await self._advance_one(organization_id, at, report)
        return report

    async def _advance_one(
        self, organization_id: uuid.UUID, now: datetime, report: SweepReport
    ) -> None:
        """Advance one organization's claim, in its own tenant-bound transaction. Never lets an
        unexpected exception propagate — see the module docstring's closing paragraph."""
        try:
            async with self._work_scope(organization_id) as session:
                await self._advance_locked(session, organization_id, now, report)
        except Exception:
            log.exception(
                "storefront.domain.sweep.advance_failed", organization_id=str(organization_id)
            )
            report.failed += 1

    async def _advance_locked(
        self,
        session: AsyncSession,
        organization_id: uuid.UUID,
        now: datetime,
        report: SweepReport,
    ) -> None:
        store = (
            await session.execute(
                select(Storefront).where(
                    Storefront.organization_id == organization_id,
                    Storefront.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()
        if store is None or store.custom_domain is None:
            # Discovered a moment ago, released since — the good outcome of the gap between
            # discovery and here, the same shape every sweep in this platform tolerates.
            report.skipped += 1
            return

        claim = await get_domain_claim_for_org(session, organization_id)
        if claim is None:
            # `storefronts.custom_domain` was set through the older `StorePut` path, which
            # creates no evidence row (ADR-0012's known seam; see `services.py`). There is
            # nothing to probe and no token to check against — the merchant has to reach
            # `PUT /storefront/store/domain` once before the sweep has any work for this row.
            report.skipped += 1
            return

        if store.custom_domain_status == "unverified" and domain.is_claim_expired(
            claim.claim_expires_at, now=now
        ):
            await self._release_expired(session, store, claim, now, report)
            return

        if claim.next_check_at > now:
            report.skipped += 1
            return

        # The check constraint guarantees a status whenever `custom_domain` is set (already
        # confirmed non-null above); spelled out for the type checker rather than trusted.
        assert store.custom_domain_status is not None
        old_status = store.custom_domain_status
        outcome = await services.advance_domain_claim(store, claim, probes=self._probes, now=now)
        await session.flush()

        action = domain.audit_action_for_transition(old_status, outcome.new_status)
        if action is None:
            report.checked += 1
            return

        record_audit(
            session,
            AuditLog,
            organization_id=organization_id,
            actor_id=None,
            acting_as=SWEEP_ACTOR,
            action=action,
            entity="storefront",
            entity_id=store.id,
            before={"custom_domain_status": old_status},
            after={
                "custom_domain_status": outcome.new_status,
                "last_error_code": claim.last_error_code,
            },
        )
        await _forget_host(store.custom_domain)
        report.transitioned += 1

    async def _release_expired(
        self,
        session: AsyncSession,
        store: Storefront,
        claim: CustomDomainClaim,
        now: datetime,
        report: SweepReport,
    ) -> None:
        """§3: an unverified claim past its 14-day expiry is released, not suspended — an
        expired claim is not a state, it is the absence of one (§6)."""
        before_domain = store.custom_domain
        store.custom_domain = None
        store.custom_domain_status = None
        await session.delete(claim)
        await session.flush()

        record_audit(
            session,
            AuditLog,
            organization_id=store.organization_id,
            actor_id=None,
            acting_as=SWEEP_ACTOR,
            action="storefront.domain.released",
            entity="storefront",
            entity_id=store.id,
            before={"custom_domain": before_domain},
            after={"custom_domain": None},
            reason="claim_expired",
        )
        await _forget_host(before_domain)
        log.info(
            "storefront.domain.expired_claim_released",
            organization_id=str(store.organization_id),
        )
        report.released += 1


async def main() -> None:
    cfg = settings()
    # Same silent-nothing failure ADR-0010 §4 names for the anonymous route: without the
    # directory role, discovery would run on the ordinary application session, bind no tenant,
    # match zero rows, and this sweep would look identical to one with no work to do.
    assert_directory_role_configured(cfg)

    engine = init_engine(cfg)
    # Every write this process makes runs on the app role under a bound tenant, so the same
    # assertion the consumers and every other sweep make applies here too.
    await assert_app_role(engine, environment=cfg.environment)
    init_directory_engine(cfg)

    sweep = DomainSweep(
        discovery_factory=get_directory_sessionmaker(),
        work_scope=lambda organization_id: session_scope(organization_id=organization_id),
        batch_size=cfg.domain_sweep_batch_size,
        poll_interval=cfg.domain_sweep_poll_interval_seconds,
    )

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        with suppress(NotImplementedError):
            # Graceful stop: the running tick finishes (each organization's transaction is
            # already atomic and short), and `run()`'s own loop exits before its next sleep
            # rather than being cancelled mid-write.
            loop.add_signal_handler(sig, sweep.stop)

    await sweep.run()


if __name__ == "__main__":
    asyncio.run(main())
