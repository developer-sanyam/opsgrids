"""Writes for metering — recording usage, and rebuilding the derived rollups.

Two things here are unlike a typical tenant service, and both are deliberate.

**Ingest is cross-tenant and opens its own transactions.** The ordinary rule is that service
functions never open or commit a transaction — a request's chokepoint (`get_tenant_session`)
does it once, bound to the caller's organization. But ingest is a *service-to-service* call
carrying no user and no tenant context, and a batch may name more than one organization. So it
does what an event consumer does: group by organization, and for each group open a transaction
and set the tenant GUC as its first statement (`set_tenant`), so the append-only `usage_events`
and `outbox` writes satisfy their row-level-security `WITH CHECK`. Getting the GUC wrong here
does not error — under RLS it silently writes nothing — which is why it is set explicitly and
per group, mirroring `session_scope(organization_id=...)`.

**Ingest is idempotent at the database.** Each event is inserted with `ON CONFLICT DO NOTHING`
against the unique `(organization_id, idempotency_key)`; the `RETURNING` tells us whether the row
was new. Posting the same key twice is one row and one event — no read-then-write race, and safe
against a duplicate inside the same batch (the first insert is visible to the second within the
transaction). Events are staged into the outbox, never published inline (ADR-0000).
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import datetime

from sqlalchemy import BigInteger, cast, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import OutboxEvent, UsageEvent, UsageRollup
from app.schemas import UsageEventIn, UsageIngestOut
from platform_core.db import set_tenant
from platform_core.events import publish_event

#: The one event metering emits. Thin on purpose: billing subscribes to it only once a plan
#: version in `catalog` prices the meter, and a thin envelope carries just enough for a consumer
#: to decide whether it cares (ADR-0004 §7).
EVT_USAGE_RECORDED = "commerce.usage.recorded"


def _service_actor(caller: str | None) -> dict[str, str]:
    """The provenance stamped on an ingested event: a service, not a user."""
    return {"type": "service", "id": caller or "unknown"}


async def _record_one(session: AsyncSession, event: UsageEventIn, *, caller: str | None) -> bool:
    """Insert one usage event idempotently. Returns True if it was newly recorded.

    Assumes the caller has already bound the transaction to `event.organization_id`. The insert
    generates the row's id application-side so `RETURNING` distinguishes a fresh insert from a
    conflict: a returned id is a new row, nothing returned is a key already seen.
    """
    new_id = uuid.uuid4()
    values: dict[str, object] = {
        "id": new_id,
        "organization_id": event.organization_id,
        "meter_key": event.meter_key,
        "subject_ref": event.subject_ref,
        "quantity": event.quantity,
        "unit": event.unit,
        "is_billable": event.is_billable,
        "idempotency_key": event.idempotency_key,
    }
    # Only set `occurred_at` when the client supplied it; otherwise the column's server_default
    # (now()) stands, which is the honest "recorded now" reading.
    if event.occurred_at is not None:
        values["occurred_at"] = event.occurred_at

    stmt = (
        pg_insert(UsageEvent)
        .values(**values)
        .on_conflict_do_nothing(index_elements=["organization_id", "idempotency_key"])
        .returning(UsageEvent.id)
    )
    inserted_id = (await session.execute(stmt)).scalar_one_or_none()
    if inserted_id is None:
        return False

    publish_event(
        session,
        OutboxEvent,
        organization_id=event.organization_id,
        event_type=EVT_USAGE_RECORDED,
        aggregate_type="usage_event",
        aggregate_id=inserted_id,
        payload={
            "organization_id": str(event.organization_id),
            "usage_event_id": str(inserted_id),
            "meter_key": event.meter_key,
            "subject_ref": str(event.subject_ref) if event.subject_ref else None,
            "quantity": event.quantity,
            "unit": event.unit,
            "is_billable": event.is_billable,
        },
        actor=_service_actor(caller),
    )
    return True


async def record_usage_batch(
    session: AsyncSession, events: list[UsageEventIn], *, caller: str | None = None
) -> UsageIngestOut:
    """Record a batch of usage events, idempotently, grouped by organization.

    One transaction per organization, each bound to that tenant before it writes — so the
    append-only insert and its `commerce.usage.recorded` event commit together under the right
    row-level-security context. `recorded + duplicates` equals the number of events posted, so a
    caller can retry the whole batch and see exactly how many were new.
    """
    by_org: dict[uuid.UUID, list[UsageEventIn]] = defaultdict(list)
    for event in events:
        by_org[event.organization_id].append(event)

    recorded = 0
    duplicates = 0
    for org_id, group in by_org.items():
        async with session.begin():
            await set_tenant(session, org_id)
            for event in group:
                if await _record_one(session, event, caller=caller):
                    recorded += 1
                else:
                    duplicates += 1

    return UsageIngestOut(recorded=recorded, duplicates=duplicates)


async def rebuild_rollup(
    session: AsyncSession,
    org_id: uuid.UUID,
    meter_key: str,
    *,
    period_start: datetime,
    period_end: datetime,
) -> UsageRollup:
    """Recompute one `(organization_id, meter_key, period)` rollup from the append-only log.

    Derived, never authoritative: this reads `usage_events` and upserts the summed result, so a
    rollup can be dropped and rebuilt at any time without touching the source of truth. The caller
    owns the transaction and its tenant binding (this is a tenant-scoped write like any other).
    """
    totals = (
        await session.execute(
            select(
                cast(func.coalesce(func.sum(UsageEvent.quantity), 0), BigInteger),
                cast(
                    func.coalesce(
                        func.sum(UsageEvent.quantity).filter(UsageEvent.is_billable.is_(True)), 0
                    ),
                    BigInteger,
                ),
                func.min(UsageEvent.unit),
            ).where(
                UsageEvent.organization_id == org_id,
                UsageEvent.meter_key == meter_key,
                UsageEvent.deleted_at.is_(None),
                UsageEvent.occurred_at >= period_start,
                UsageEvent.occurred_at < period_end,
            )
        )
    ).one()
    total_quantity, billable_quantity, unit = totals

    stmt = (
        pg_insert(UsageRollup)
        .values(
            id=uuid.uuid4(),
            organization_id=org_id,
            meter_key=meter_key,
            unit=unit or "",
            period_start=period_start,
            period_end=period_end,
            total_quantity=int(total_quantity),
            billable_quantity=int(billable_quantity),
        )
        .on_conflict_do_update(
            index_elements=["organization_id", "meter_key", "period_start"],
            set_={
                "unit": unit or "",
                "period_end": period_end,
                "total_quantity": int(total_quantity),
                "billable_quantity": int(billable_quantity),
            },
        )
        .returning(UsageRollup.id)
    )
    rollup_id = (await session.execute(stmt)).scalar_one()
    rebuilt = await session.get(UsageRollup, rollup_id)
    if rebuilt is None:  # pragma: no cover - the row was just upserted in this transaction
        raise RuntimeError("rollup vanished immediately after upsert")
    return rebuilt
