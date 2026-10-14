"""Auto-generated item codes (SKUs): `ITM-000001`, `ITM-000002`, ...

A person who leaves the SKU blank gets the next number in their organization's series. The number
comes from a per-organization counter row (`item_code_counters`), taken with one
`INSERT ... ON CONFLICT DO UPDATE ... RETURNING` inside the create's own transaction:

* **Concurrency-safe.** The upsert locks the counter row until commit, so two simultaneous creates
  queue and receive different numbers. No application lock, and no global Postgres SEQUENCE —
  that would be shared across tenants and reveal how many items other organizations hold.
* **Per-organization.** The row is keyed by `organization_id`; org A's counter never moves org B's.
* **Gap-tolerant.** A code somebody already typed by hand (as an item SKU, a variant SKU, or
  another item's barcode — the three namespaces a SKU must not collide with) is skipped. A
  rollback also rolls the counter back, so a failed create does not burn a number.
* **Fast over a crowded range.** If the next number is taken, the counter jumps past the highest
  `ITM-<digits>` code already present in one query, instead of walking one number at a time.

The format is a constant. There is no per-organization setting to hang it on yet, and this slice
does not build one.
"""

from __future__ import annotations

import uuid

from sqlalchemy import BigInteger, cast, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.inventory import selectors as inv_selectors
from app.models import SCHEMA, InventoryItem, ItemVariant
from platform_core.errors import ConflictProblem

SERIES = "item"
PREFIX = "ITM-"
#: Zero-padded width. A number past 999 999 simply grows a digit; nothing truncates.
WIDTH = 6
#: How many times to re-draw after a collision before giving up. Each re-draw first jumps the
#: counter past every existing `ITM-<digits>` code, so one is enough unless another writer is
#: inserting codes by hand at the same moment; the rest is slack, not a loop that walks a range.
_MAX_ATTEMPTS = 5

_PATTERN = f"^{PREFIX}[0-9]+$"


def format_item_code(number: int) -> str:
    """`ITM-000042` for 42."""
    return f"{PREFIX}{number:0{WIDTH}d}"


async def _draw(session: AsyncSession, org_id: uuid.UUID) -> int:
    """Take the next number: insert the organization's counter at 1, or add 1 to it."""
    row = await session.execute(
        text(
            f"""
            INSERT INTO {SCHEMA}.item_code_counters (id, organization_id, series, last_value)
            VALUES (:id, :org, :series, 1)
            ON CONFLICT (organization_id, series)
            DO UPDATE SET last_value = {SCHEMA}.item_code_counters.last_value + 1
            RETURNING last_value
            """  # noqa: S608 — only the SCHEMA constant is interpolated
        ),
        {"id": uuid.uuid4(), "org": org_id, "series": SERIES},
    )
    return int(row.scalar_one())


async def _highest_existing(session: AsyncSession, org_id: uuid.UUID) -> int:
    """The largest `n` among this organization's live `ITM-<n>` item and variant SKUs."""
    best = 0
    for column, org_col, deleted in (
        (InventoryItem.sku, InventoryItem.organization_id, InventoryItem.deleted_at),
        (ItemVariant.sku, ItemVariant.organization_id, ItemVariant.deleted_at),
    ):
        suffix = cast(func.substr(column, len(PREFIX) + 1), BigInteger)
        value = (
            await session.execute(
                select(func.max(suffix)).where(
                    org_col == org_id, deleted.is_(None), column.op("~")(_PATTERN)
                )
            )
        ).scalar_one_or_none()
        best = max(best, int(value or 0))
    return best


async def _taken(session: AsyncSession, org_id: uuid.UUID, code: str) -> bool:
    """Whether `code` is already some item's SKU, some variant's SKU, or some item's barcode —
    the same three checks `create_inventory_item` applies to a typed SKU."""
    return bool(
        await inv_selectors.get_inventory_by_sku(session, org_id, code)
        or await inv_selectors.get_item_variant_by_sku(session, org_id, code)
        or await inv_selectors.item_barcode_taken(session, org_id, code, only_barcode=True)
    )


async def next_item_code(session: AsyncSession, org_id: uuid.UUID) -> str:
    """The organization's next free item code, advancing its counter. Call inside the transaction
    that will insert the item; the code is only reserved by that transaction committing."""
    for _ in range(_MAX_ATTEMPTS):
        code = format_item_code(await _draw(session, org_id))
        if not await _taken(session, org_id, code):
            return code
        floor = await _highest_existing(session, org_id)
        if floor:
            await session.execute(
                text(
                    f"""
                    UPDATE {SCHEMA}.item_code_counters
                    SET last_value = GREATEST(last_value, :floor)
                    WHERE organization_id = :org AND series = :series
                    """  # noqa: S608 — only the SCHEMA constant is interpolated
                ),
                {"floor": floor, "org": org_id, "series": SERIES},
            )
    raise ConflictProblem("Could not generate a free item code; please try again")
