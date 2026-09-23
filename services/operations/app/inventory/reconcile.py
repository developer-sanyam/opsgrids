"""Ledger ↔ balance reconciliation (ADR-0014 §3). Detects and records drift; never repairs it.

Two invariants, per bucket:

* `inventory_balances.quantity_milli == SUM(inventory_ledger.quantity_milli)` — the projection
  agrees with the record it projects. A bucket present on one side only is drift too.
* `inventory_balances.reserved_milli == SUM(open remainder of active reservations)`.

Both are computed in **one statement**, so they share one snapshot: a move committing mid-check
cannot make a healthy bucket look drifted. Each finding becomes (or refreshes) one open
`inventory_discrepancies` row. A new one also stages an
`operations.inventory.inconsistency_detected` event and logs at error level — that is what an
alert hangs off. Nothing here writes a balance: a projection that disagrees with its ledger is a
bug to understand, and "fixing" it automatically would destroy the evidence.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.inventory.quantity import format_qty
from app.models import SCHEMA, OutboxEvent
from platform_core.events import publish_event
from platform_core.logging import get_logger

log = get_logger("operations.inventory.reconcile")

EVT_INCONSISTENCY = "operations.inventory.inconsistency_detected"

#: Nullable lot/roll ids are compared through this sentinel: a FULL JOIN only accepts
#: hash- or merge-joinable conditions, which `IS NOT DISTINCT FROM` is not.
_DRIFT_SQL = text(
    f"""
    WITH ledger AS (
        SELECT inventory_item_id, location_id, status, lot_id, roll_id,
               SUM(quantity_milli)::bigint AS total
          FROM {SCHEMA}.inventory_ledger
         WHERE organization_id = :org
         GROUP BY inventory_item_id, location_id, status, lot_id, roll_id
    ),
    balances AS (
        SELECT id, inventory_item_id, location_id, status, lot_id, roll_id,
               quantity_milli, reserved_milli
          FROM {SCHEMA}.inventory_balances
         WHERE organization_id = :org
    ),
    reservations AS (
        SELECT balance_id, SUM(quantity_milli - consumed_milli - released_milli)::bigint AS total
          FROM {SCHEMA}.inventory_reservations
         WHERE organization_id = :org AND status = 'active'
         GROUP BY balance_id
    ),
    checked AS (SELECT count(*) AS n FROM balances)
    SELECT 'balance' AS kind,
           COALESCE(b.inventory_item_id, l.inventory_item_id) AS inventory_item_id,
           COALESCE(b.location_id, l.location_id) AS location_id,
           COALESCE(b.status, l.status) AS status,
           COALESCE(b.lot_id, l.lot_id) AS lot_id,
           COALESCE(b.roll_id, l.roll_id) AS roll_id,
           COALESCE(b.quantity_milli, 0) AS stored_milli,
           COALESCE(l.total, 0) AS expected_milli,
           (SELECT n FROM checked) AS checked
      FROM balances b
      FULL JOIN ledger l
        ON b.inventory_item_id = l.inventory_item_id
       AND b.location_id = l.location_id
       AND b.status = l.status
       AND COALESCE(b.lot_id, :none) = COALESCE(l.lot_id, :none)
       AND COALESCE(b.roll_id, :none) = COALESCE(l.roll_id, :none)
     WHERE COALESCE(b.quantity_milli, 0) <> COALESCE(l.total, 0)
    UNION ALL
    SELECT 'reserved', b.inventory_item_id, b.location_id, b.status, b.lot_id, b.roll_id,
           b.reserved_milli, COALESCE(r.total, 0), (SELECT n FROM checked)
      FROM balances b
      LEFT JOIN reservations r ON r.balance_id = b.id
     WHERE b.reserved_milli <> COALESCE(r.total, 0)
    UNION ALL
    SELECT 'none', NULL, NULL, NULL, NULL, NULL, NULL, NULL, (SELECT n FROM checked)
    """  # noqa: S608 — only the SCHEMA constant is interpolated; values are bound
)

_RECORD_SQL = text(
    f"""
    INSERT INTO {SCHEMA}.inventory_discrepancies
        (id, organization_id, kind, inventory_item_id, location_id, status, lot_id, roll_id,
         stored_milli, expected_milli, run_id, detected_at, last_seen_at, resolution_note)
    VALUES (:id, :org, :kind, :item, :loc, :status, :lot, :roll, :stored, :expected, :run,
            now(), now(), '')
    ON CONFLICT (organization_id, kind, inventory_item_id, location_id, status, lot_id, roll_id)
        WHERE resolved_at IS NULL
    DO UPDATE SET stored_milli = EXCLUDED.stored_milli,
                  expected_milli = EXCLUDED.expected_milli,
                  run_id = EXCLUDED.run_id,
                  last_seen_at = now()
    RETURNING id, (xmax = 0) AS inserted
    """  # noqa: S608 — only the SCHEMA constant is interpolated; values are bound
)


@dataclass(slots=True)
class Finding:
    kind: str
    inventory_item_id: uuid.UUID
    location_id: uuid.UUID
    status: str
    lot_id: uuid.UUID | None
    roll_id: uuid.UUID | None
    stored_milli: int
    expected_milli: int
    discrepancy_id: uuid.UUID | None = None
    new: bool = False


@dataclass(slots=True)
class ReconcileReport:
    organization_id: uuid.UUID
    run_id: uuid.UUID
    buckets_checked: int = 0
    findings: list[Finding] = field(default_factory=list)

    @property
    def new(self) -> list[Finding]:
        return [f for f in self.findings if f.new]

    @property
    def clean(self) -> bool:
        return not self.findings


async def reconcile_organization(
    session: AsyncSession, organization_id: uuid.UUID, *, run_id: uuid.UUID | None = None
) -> ReconcileReport:
    """Check one organization's stock and record what disagrees. Runs in the caller's
    transaction, which must be bound to `organization_id` (the tables are tenant-policied)."""
    report = ReconcileReport(organization_id=organization_id, run_id=run_id or uuid.uuid4())
    rows = (
        await session.execute(_DRIFT_SQL, {"org": organization_id, "none": uuid.UUID(int=0)})
    ).mappings()
    for row in rows:
        report.buckets_checked = row["checked"]
        if row["kind"] == "none":
            continue
        report.findings.append(
            Finding(
                kind=row["kind"],
                inventory_item_id=row["inventory_item_id"],
                location_id=row["location_id"],
                status=row["status"],
                lot_id=row["lot_id"],
                roll_id=row["roll_id"],
                stored_milli=row["stored_milli"],
                expected_milli=row["expected_milli"],
            )
        )

    for finding in report.findings:
        recorded = (
            await session.execute(
                _RECORD_SQL,
                {
                    "id": uuid.uuid4(),
                    "org": organization_id,
                    "kind": finding.kind,
                    "item": finding.inventory_item_id,
                    "loc": finding.location_id,
                    "status": finding.status,
                    "lot": finding.lot_id,
                    "roll": finding.roll_id,
                    "stored": finding.stored_milli,
                    "expected": finding.expected_milli,
                    "run": report.run_id,
                },
            )
        ).one()
        finding.discrepancy_id, finding.new = recorded.id, recorded.inserted
        if not finding.new:
            continue
        detail = {
            "discrepancy_id": str(finding.discrepancy_id),
            "kind": finding.kind,
            "inventory_item_id": str(finding.inventory_item_id),
            "location_id": str(finding.location_id),
            "status": finding.status,
            "lot_id": str(finding.lot_id) if finding.lot_id else None,
            "roll_id": str(finding.roll_id) if finding.roll_id else None,
            "stored": format_qty(finding.stored_milli),
            "expected": format_qty(finding.expected_milli),
            "stored_milli": finding.stored_milli,
            "expected_milli": finding.expected_milli,
        }
        log.error(
            "inventory.reconcile.drift",
            organization_id=str(organization_id),
            run_id=str(report.run_id),
            **detail,
        )
        publish_event(
            session,
            OutboxEvent,
            organization_id=organization_id,
            event_type=EVT_INCONSISTENCY,
            aggregate_type="inventory_discrepancy",
            aggregate_id=finding.discrepancy_id,
            payload={
                "organization_id": str(organization_id),
                "run_id": str(report.run_id),
                **detail,
            },
            actor={"type": "system", "id": "operations.inventory.reconcile"},
        )
    return report
