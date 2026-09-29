"""The scheduled channel sync sweep (Fulfilment plan F11) — the same discovery/work split
`app.cron.ReconcileSweep`'s own module docstring documents: **discovery** runs on the BYPASSRLS
outbox role, because its only question — *which connections are `connected`?* — has no tenant to
bind; every read and write that matters (the channel row itself, the order it imports, the
inventory it pushes) runs on the app role inside a tenant-bound session.

Order import is poll-based rather than webhook-based (plan item 3's own allowance: "on a
scheduled pull, if that's more realistic"), extended here to inventory push too — a shop's
`available_milli` changes on every pick, pack and dispatch scan, and pushing on each one would
flood a real provider's rate limit during a picking wave; a short poll interval (default 5
minutes, `Settings.channel_sync_interval_seconds`) is what a real integration would actually run,
and `push_inventory_for_channel` already only pushes a SKU whose number has actually moved since
it was last told.

One connection's failure — a timeout, an expired credential — is counted and logged, never
allowed to stop the sweep from reaching the next connection, the identical isolation
`ReconcileSweep.tick` gives one organization's failure.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.channels import services
from app.models import SCHEMA
from platform_core.logging import get_logger
from platform_core.supervise import GuardedSweep

log = get_logger("operations.channels.sweep")

WorkScope = Callable[[uuid.UUID], AbstractAsyncContextManager[AsyncSession]]

_CONNECTED_CHANNELS_SQL = text(
    f"""
    SELECT organization_id, id FROM {SCHEMA}.channels
    WHERE status = 'connected' AND deleted_at IS NULL
    """  # noqa: S608 — only the SCHEMA constant is interpolated
)

#: Once every five minutes unless told otherwise — short enough that a real provider's rate limit
#: is respected by `push_inventory_for_channel`'s own diffing rather than by a long poll interval.
DEFAULT_INTERVAL_SECONDS = 300


@dataclass(slots=True)
class SweepReport:
    run_id: uuid.UUID
    connections: int = 0
    orders_imported: int = 0
    orders_rejected: int = 0
    inventory_skus_pushed: int = 0
    failed: int = 0


class ChannelSyncSweep(GuardedSweep):
    """One pass polls every connected channel for new orders and pushes every mapped SKU whose
    `available_milli` has moved. Injected session factories, so a test drives the real code
    against real PostgreSQL with nothing patched — the same shape `ReconcileSweep` already uses.
    """

    def __init__(
        self,
        *,
        discovery_factory: async_sessionmaker[AsyncSession],
        work_scope: WorkScope,
        interval: float = DEFAULT_INTERVAL_SECONDS,
    ) -> None:
        super().__init__(poll_interval=interval)
        self._discovery_factory = discovery_factory
        self._work_scope = work_scope

    @property
    def _log_name(self) -> str:
        return "channels.sync"

    async def tick(self) -> SweepReport:
        report = SweepReport(run_id=uuid.uuid4())
        async with self._discovery_factory() as discovery:
            rows = [
                (row.organization_id, row.id)
                for row in await discovery.execute(_CONNECTED_CHANNELS_SQL)
            ]
        report.connections = len(rows)

        for organization_id, channel_id in rows:
            try:
                async with self._work_scope(organization_id) as session:
                    await self._sync_one(session, organization_id, channel_id, report)
            except Exception:
                report.failed += 1
                log.exception(
                    "channels.sync.connection_failed",
                    organization_id=str(organization_id),
                    channel_id=str(channel_id),
                    run_id=str(report.run_id),
                )
                continue

        level = log.error if report.failed else log.info
        level(
            "channels.sync.pass",
            run_id=str(report.run_id),
            connections=report.connections,
            orders_imported=report.orders_imported,
            orders_rejected=report.orders_rejected,
            inventory_skus_pushed=report.inventory_skus_pushed,
            failed=report.failed,
        )
        return report

    async def _sync_one(
        self,
        session: AsyncSession,
        organization_id: uuid.UUID,
        channel_id: uuid.UUID,
        report: SweepReport,
    ) -> None:
        """`sync_orders_for_channel` and `push_inventory_for_channel` already catch every
        `ChannelClient` exception themselves and record it on the connection's own health
        (`app.channels.services._record_outcome`) rather than propagating it, so `tick`'s own
        per-connection `try`/`except` mainly exists for a genuine defect (a bad query, a bug) —
        the one exception this method itself can still raise is `load_client_for_channel`'s own
        `NotFoundProblem`/`ValidationProblem`, for the rare race where a connection was
        disconnected between discovery and this call; that, too, is simply counted as one failed
        connection for this pass rather than stopping it.
        """
        ctx = services.service_ctx(organization_id)
        _channel, client = await services.load_client_for_channel(session, ctx, channel_id)
        try:
            order_summary = await services.sync_orders_for_channel(session, ctx, channel_id, client)
            report.orders_imported += order_summary.succeeded
            report.orders_rejected += order_summary.rejected
            inventory_summary = await services.push_inventory_for_channel(
                session, ctx, channel_id, client
            )
            report.inventory_skus_pushed += inventory_summary.succeeded
        finally:
            aclose = getattr(client, "aclose", None)
            if aclose is not None:
                await aclose()
