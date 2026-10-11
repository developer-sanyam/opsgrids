"""Document-number series for job work: the order, the receipt and — the one with a statutory
shape — the delivery challan.

Each series is serialised per organization with a transaction-scoped advisory lock and then reads
the current maximum, the same shape `app.orders.services.next_order_number` and
`app.services.next_po_number` use. Three properties follow, and the challan needs all three:

* **Race-safe.** Two concurrent issues in one organization queue on the lock; the second sees the
  first's number only after it commits, so no number is handed out twice (and the unique index on
  `(organization_id, challan_number)` is the backstop that turns a bug into an error, not a
  duplicate).
* **Gapless.** The number is computed from committed rows, inside the transaction that will
  insert it. A request that rolls back after asking for a number never consumed one.
* **Immutable.** A number is never recomputed or reassigned: the challan row is frozen by a
  trigger, and nothing here writes to an existing row.

A challan series restarts each Indian financial year and stays within 16 characters, e.g.
`DC/26-27/0001` (CGST Rule 55 requires a consecutive serial number, unique for the year).
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any

from sqlalchemy import Integer, cast, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.jobwork.domain import financial_year, ist_today
from app.models import Challan, JobWorkOrder, JobWorkReceipt

#: Zero-padded to four digits, but the sequence itself is read numerically, so the 10 000th
#: number of a series sorts and increments correctly rather than colliding with the 1 000th.
_WIDTH = 4


async def _next(
    session: AsyncSession,
    organization_id: uuid.UUID,
    *,
    series: str,
    prefix: str,
    column: Any,
    organization_column: Any,
) -> str:
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
        {"key": f"{organization_id}:{series}"},
    )
    sequence = cast(func.substr(column, len(prefix) + 1), Integer)
    current = (
        await session.execute(
            select(func.max(sequence)).where(
                organization_column == organization_id, column.like(f"{prefix}%")
            )
        )
    ).scalar_one_or_none()
    return f"{prefix}{(current or 0) + 1:0{_WIDTH}d}"


async def next_order_number(
    session: AsyncSession, organization_id: uuid.UUID, *, today: date | None = None
) -> str:
    """`JW-{yyyy}-{seq:04d}`, per organization per calendar year."""
    year = (today or date.today()).year
    return await _next(
        session,
        organization_id,
        series=f"jobwork_order:{year}",
        prefix=f"JW-{year}-",
        column=JobWorkOrder.number,
        organization_column=JobWorkOrder.organization_id,
    )


async def next_receipt_number(
    session: AsyncSession, organization_id: uuid.UUID, *, today: date | None = None
) -> str:
    """`JWR-{yyyy}-{seq:04d}` — one number per receipt document (a document covers one row per
    material, all carrying the same number)."""
    year = (today or date.today()).year
    return await _next(
        session,
        organization_id,
        series=f"jobwork_receipt:{year}",
        prefix=f"JWR-{year}-",
        column=JobWorkReceipt.receipt_no,
        organization_column=JobWorkReceipt.organization_id,
    )


async def next_challan_number(
    session: AsyncSession, organization_id: uuid.UUID, *, today: date | None = None
) -> str:
    """`DC/{fy}/{seq:04d}` — a financial-year series, e.g. `DC/26-27/0001`."""
    fy = financial_year(today or ist_today())
    return await _next(
        session,
        organization_id,
        series=f"jobwork_challan:{fy}",
        prefix=f"DC/{fy}/",
        column=Challan.challan_number,
        organization_column=Challan.organization_id,
    )
