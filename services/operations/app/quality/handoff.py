"""The quality side of a QC hand-off: goods have been parked in `QUALITY_HOLD` by another process
and an inspection must be opened over them (inventory plan Phases 4 and 6).

Today there is one producer: a job-work receipt on an order flagged `qc_required` emits
`operations.jobwork.receipt.qc_requested` (`app.jobwork.services`) after posting the good quantity
into `QUALITY_HOLD`. The job-work package never imports this one and never waits for it; the event
is the whole contract, and `app.consumers` binds it to `open_for_job_work_receipt`. A goods-receipt
hand-off from purchasing is the same shape and is added here when Phase 2 lands (see
`docs/inventory/quality.md`).

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

import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Inspection
from app.quality import services
from app.quality.schemas import InspectionIn
from platform_core.auth import Principal, Scope, ScopeContext
from platform_core.errors import ConflictProblem, NotFoundProblem, ValidationProblem

#: The permission the hand-off acts under — the inspector's own: it opens an inspection and
#: records nothing else. Not a grant to any user; the principal below is the system's.
SYSTEM_PERMISSION = "operations.quality.inspection.inspect"

REFERENCE_TYPE = "job_work_order"

#: `app.jobwork.services.EVT_QC_REQUESTED`, copied rather than imported: the two packages stay
#: independent and the event name is the contract (a test pins the two together).
EVT_JOB_WORK_QC_REQUESTED = "operations.jobwork.receipt.qc_requested"


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
