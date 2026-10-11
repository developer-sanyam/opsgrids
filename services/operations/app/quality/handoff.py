"""The quality side of a QC hand-off: goods have been parked in `QUALITY_HOLD` by another process
and an inspection must be opened over them (inventory plan Phases 4 and 6).

Two producers:

* a job-work receipt on an order flagged `qc_required` emits
  `operations.jobwork.receipt.qc_requested` (`app.jobwork.services`) after posting the good quantity
  into `QUALITY_HOLD`; `app.consumers` binds it to `open_for_job_work_receipt`;
* a goods receipt (`app.receiving.receipts`) emits `operations.stock.received`; a line of an item
  that tracks quality was received into `QUALITY_HOLD`, and `app.consumers` binds the event to
  `open_for_goods_receipt`, which opens one inspection per held bucket the receipt's ledger move
  created (a lot, and each roll of it).

Neither producing package imports this one or waits for it; the event is the whole contract.

**Idempotent twice over.** The consumer marks the event id processed in the same transaction; and
each inspection is created under a deterministic `Idempotency-Key`
(`jw-qc:<order>:<receipt>:<material>`), so a redelivery that somehow bypassed the processed-event
check replays the first inspection instead of opening a second one.

**A refusal never poisons the queue.** The hand-off is best effort per line: a line whose stock is
no longer on hold (someone already moved or inspected it), or whose item, location, lot or roll has
gone, is skipped and reported, inside its own savepoint so the other lines still get their
inspection. The consumer logs what was skipped; redelivering would only refuse again.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.inventory.quantity import format_qty
from app.models import AuditLog, Inspection, LedgerEntry
from app.quality import services
from app.quality.schemas import InspectionIn
from platform_core.audit import record_audit
from platform_core.auth import Principal, Scope, ScopeContext
from platform_core.errors import ConflictProblem, NotFoundProblem, ValidationProblem

#: The permission the hand-off acts under — the inspector's own: it opens an inspection and
#: records nothing else. Not a grant to any user; the principal below is the system's.
SYSTEM_PERMISSION = "operations.quality.inspection.inspect"

REFERENCE_TYPE = "job_work_order"

#: `app.jobwork.services.EVT_QC_REQUESTED`, copied rather than imported: the two packages stay
#: independent and the event name is the contract (a test pins the two together).
EVT_JOB_WORK_QC_REQUESTED = "operations.jobwork.receipt.qc_requested"

GOODS_RECEIPT_REFERENCE_TYPE = "goods_receipt"

#: `app.receiving.receipts.EVT_STOCK_RECEIVED`, copied for the same reason (a test pins the two).
EVT_STOCK_RECEIVED = "operations.stock.received"


def service_ctx(organization_id: uuid.UUID) -> ScopeContext:
    principal = Principal(
        sub=uuid.UUID(int=0),
        organization_id=organization_id,
        membership_id=None,
        perms=[f"{SYSTEM_PERMISSION}:ORG"],
        entitlements=["operations"],
    )
    return ScopeContext(principal=principal, scope=Scope.ORG, permission=SYSTEM_PERMISSION)


@dataclass(slots=True)
class HandoffResult:
    opened: list[Inspection] = field(default_factory=list)
    #: `(material_id, reason)` for every line that was skipped.
    skipped: list[tuple[str, str]] = field(default_factory=list)


async def open_for_job_work_receipt(
    session: AsyncSession, ctx: ScopeContext, payload: dict[str, Any]
) -> HandoffResult:
    """One `incoming` inspection per line that brought good quantity back into `QUALITY_HOLD`
    (`hold_stock=False`: the stock is already on hold, verified against the balance less what
    other open inspections claim). Rejected quantity went to `REJECTED` and is not inspected."""
    result = HandoffResult()
    order_id = str(payload["order_id"])
    receipt_no = str(payload["receipt_no"])
    supplier_id = uuid.UUID(str(payload["supplier_id"]))
    for line in payload["lines"]:
        material_id = str(line["material_id"])
        if line.get("status") != "QUALITY_HOLD" or Decimal(str(line["received"])) == 0:
            continue
        body = InspectionIn(
            kind="incoming",
            item_id=uuid.UUID(str(line["item_id"])),
            location_id=uuid.UUID(str(line["location_id"])),
            quantity=str(line["received"]),
            lot_id=uuid.UUID(str(line["lot_id"])) if line.get("lot_id") else None,
            roll_id=uuid.UUID(str(line["roll_id"])) if line.get("roll_id") else None,
            supplier_id=supplier_id,
            reference_type=REFERENCE_TYPE,
            reference_id=uuid.UUID(order_id),
            hold_stock=False,
            notes=f"Returned from job work {payload['order_number']}, receipt {receipt_no}"[:500],
        )
        try:
            async with session.begin_nested():
                inspection = await services.create_inspection(
                    session,
                    ctx,
                    body,
                    idempotency_key=f"jw-qc:{order_id}:{receipt_no}:{material_id}"[:128],
                )
        except (ConflictProblem, NotFoundProblem, ValidationProblem) as exc:
            result.skipped.append((material_id, exc.detail))
            continue
        result.opened.append(inspection)
    return result


async def open_for_goods_receipt(
    session: AsyncSession, ctx: ScopeContext, payload: dict[str, Any]
) -> HandoffResult:
    """One `incoming` inspection per `QUALITY_HOLD` bucket a goods receipt put on the books, for
    the lines whose item tracks quality (`hold_stock=False`: the stock is already on hold).

    The buckets come from the receipt's own ledger move (`payload["move_id"]`, read under this
    organization), not from the event's roll list: a roll's measured quantity is not in the event,
    and a roll-tracked line is one bucket per roll. A line the receipt put on hold only because the
    receiver asked (`hold` on a non-quality item) has no inspection to open and is skipped, as is a
    service line or a receipt that posted no stock (`move_id` null)."""
    result = HandoffResult()
    if payload.get("move_id") is None:
        return result
    org_id = ctx.organization_id
    receipt_id = uuid.UUID(str(payload["goods_receipt_id"]))
    supplier_id = uuid.UUID(str(payload["supplier_id"]))
    move_id = uuid.UUID(str(payload["move_id"]))

    # `track_quality` is the receipt's own snapshot, so a later change to the item cannot drop it.
    tracked = {
        uuid.UUID(str(line["inventory_item_id"]))
        for line in payload["lines"]
        if line.get("inventory_item_id")
        and line.get("status") == "QUALITY_HOLD"
        and line.get("track_quality")
    }
    if not tracked:
        return result
    entries = (
        (
            await session.execute(
                select(LedgerEntry)
                .where(
                    LedgerEntry.organization_id == org_id,
                    LedgerEntry.move_id == move_id,
                    LedgerEntry.status == "QUALITY_HOLD",
                    LedgerEntry.quantity_milli > 0,
                    LedgerEntry.inventory_item_id.in_(tracked),
                )
                .order_by(LedgerEntry.created_at, LedgerEntry.id)
            )
        )
        .scalars()
        .all()
    )
    for entry in entries:
        bucket = ":".join(
            str(part or "-")
            for part in (entry.inventory_item_id, entry.location_id, entry.lot_id, entry.roll_id)
        )
        label = f"{entry.inventory_item_id}/{entry.lot_id or '-'}/{entry.roll_id or '-'}"
        body = InspectionIn(
            kind="incoming",
            item_id=entry.inventory_item_id,
            location_id=entry.location_id,
            quantity=format_qty(entry.quantity_milli),
            lot_id=entry.lot_id,
            roll_id=entry.roll_id,
            supplier_id=supplier_id,
            reference_type=GOODS_RECEIPT_REFERENCE_TYPE,
            reference_id=receipt_id,
            hold_stock=False,
            notes=f"Received on goods receipt {payload['number']}"[:500],
        )
        key = "gr-qc:" + hashlib.sha256(f"{receipt_id}:{bucket}".encode()).hexdigest()
        try:
            async with session.begin_nested():
                inspection = await services.create_inspection(
                    session, ctx, body, idempotency_key=key
                )
        except (ConflictProblem, NotFoundProblem, ValidationProblem) as exc:
            result.skipped.append((label, exc.detail))
            continue
        result.opened.append(inspection)
    if result.skipped:
        # Observable, not just logged: a held bucket nobody inspects is stock stuck on hold.
        record_audit(
            session,
            AuditLog,
            organization_id=org_id,
            actor_id=ctx.principal.sub,
            action="goods_receipt.qc_handoff_skipped",
            entity="goods_receipt",
            entity_id=receipt_id,
            after={"skipped": [{"bucket": b, "reason": why} for b, why in result.skipped]},
        )
    return result
