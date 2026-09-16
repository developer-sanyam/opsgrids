"""Writes for approval.

Business rules, audit rows and outbox rows. Nothing here opens or commits a transaction: the
consumer and the backfill each take their session from `session_scope(organization_id=...)`,
which binds the transaction to the tenant first — so the state change and its idempotency marker
commit or roll back together.

This slice has exactly one write: the version-gated upsert of one `hierarchy_nodes` row
(ADR-0006 §2, data model §1.1). It is shared by the event consumer and the one-time backfill so
the ordering rule is enforced in exactly one place. `upsert_hierarchy_nodes_bulk` is the same
write, batched — the backfill's page loop is the only caller with more than one row at a time.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast

from sqlalchemy import CursorResult, func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import HIERARCHY_STATUSES, HierarchyNode


@dataclass(frozen=True, slots=True)
class HierarchyNodeWrite:
    """One row's worth of the version-gated upsert.

    Mirrors `upsert_hierarchy_node`'s keyword arguments exactly, minus `organization_id`, which
    `upsert_hierarchy_nodes_bulk` takes once for the whole batch rather than once per row.
    """

    employee_id: uuid.UUID
    reporting_manager_id: uuid.UUID | None
    dotted_line_manager_id: uuid.UUID | None
    status: str
    display_name: str
    employee_no: str
    location_code: str | None
    source_version: int
    source_occurred_at: datetime


async def upsert_hierarchy_node(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    employee_id: uuid.UUID,
    reporting_manager_id: uuid.UUID | None,
    dotted_line_manager_id: uuid.UUID | None,
    status: str,
    display_name: str,
    employee_no: str,
    location_code: str | None,
    source_version: int,
    source_occurred_at: datetime,
) -> bool:
    """Apply one hierarchy state, if and only if it is newer than what is stored.

    The ordering rule (ADR-0006 §2, "a version, not a timestamp"; data model §1.1): apply an
    event or a backfill row only when its `source_version` is **strictly greater** than the
    value already stored for `(organization_id, employee_id)`. Equal is a duplicate — a
    redelivered event, or a backfill page that re-covers ground the live stream already applied
    — and is dropped without error, which is what makes both the consumer and the backfill
    idempotent under replay. The caller (never this function) is responsible for refusing a row
    whose version is altogether missing, before it ever reaches here — see
    `app/consumers.py` and `app/backfill.py`.

    Returns `True` when the row was written (an insert, or an update that passed the version
    gate) and `False` when the incoming state was dropped as stale or equal. Callers use this for
    logging and for tests; it is never itself a reason to raise, because a dropped stale event is
    the intended outcome, not a failure (data model §1.1, verified: "a replayed or reordered
    event updates zero rows and the consumer treats that as success").

    A single-row call through `upsert_hierarchy_nodes_bulk` — the consumer only ever has one row
    per message, so it pays no round-trip cost for sharing the batched statement.
    """
    applied = await upsert_hierarchy_nodes_bulk(
        session,
        organization_id=organization_id,
        nodes=[
            HierarchyNodeWrite(
                employee_id=employee_id,
                reporting_manager_id=reporting_manager_id,
                dotted_line_manager_id=dotted_line_manager_id,
                status=status,
                display_name=display_name,
                employee_no=employee_no,
                location_code=location_code,
                source_version=source_version,
                source_occurred_at=source_occurred_at,
            )
        ],
    )
    return applied > 0


async def upsert_hierarchy_nodes_bulk(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    nodes: Sequence[HierarchyNodeWrite],
) -> int:
    """The same version-gated upsert as `upsert_hierarchy_node`, applied to a whole batch in one
    round trip.

    Written for the backfill's page loop (`app/backfill.py`): it used to call
    `upsert_hierarchy_node` once per item inside the page's transaction — a database round trip
    per employee inside a `for` loop, the shape CLAUDE.md calls a bug regardless of which layer
    it is in. A page is already one transaction (ADR-0006 §2.1's "one transaction per page, on
    approval's side"); batching its rows into one multi-row `INSERT ... ON CONFLICT` keeps that
    boundary exactly and changes nothing about the version gate, because PostgreSQL evaluates the
    `ON CONFLICT ... WHERE` clause per conflicting row, independently of how many other rows are
    in the same statement — one employee's stale version is dropped exactly as it would be alone.

    Returns the number of rows actually written (inserted, or updated because their version
    passed the gate): PostgreSQL's own command-tag count for `INSERT ... ON CONFLICT DO UPDATE
    ... WHERE` already excludes rows whose `WHERE` failed, so summing this across pages is
    exactly what summing `upsert_hierarchy_node`'s per-call booleans used to produce.
    """
    if not nodes:
        return 0

    for node in nodes:
        if node.status not in HIERARCHY_STATUSES:
            # A status outside the closed vocabulary would fail the table's own CHECK, but
            # refusing it here keeps a malformed payload from ever reaching the database as a
            # constraint violation that looks like a bug rather than a data problem upstream.
            raise ValueError(f"unknown hierarchy status: {node.status!r}")

    stmt = pg_insert(HierarchyNode).values(
        [
            {
                "organization_id": organization_id,
                "employee_id": node.employee_id,
                "reporting_manager_id": node.reporting_manager_id,
                "dotted_line_manager_id": node.dotted_line_manager_id,
                "status": node.status,
                "display_name": node.display_name,
                "employee_no": node.employee_no,
                "location_code": node.location_code,
                "source_version": node.source_version,
                "source_occurred_at": node.source_occurred_at,
            }
            for node in nodes
        ]
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[HierarchyNode.organization_id, HierarchyNode.employee_id],
        set_={
            "reporting_manager_id": stmt.excluded.reporting_manager_id,
            "dotted_line_manager_id": stmt.excluded.dotted_line_manager_id,
            "status": stmt.excluded.status,
            "display_name": stmt.excluded.display_name,
            "employee_no": stmt.excluded.employee_no,
            "location_code": stmt.excluded.location_code,
            "source_version": stmt.excluded.source_version,
            "source_occurred_at": stmt.excluded.source_occurred_at,
            # `TimestampMixin.updated_at`'s `onupdate=func.now()` is a Core/ORM `Update()`
            # default; `on_conflict_do_update`'s `set_` is taken literally and does not consult
            # it, so without this line the column silently keeps its insert-time value across
            # every later upsert (data model §1.1 specifies it moves on every applied change).
            "updated_at": func.now(),
        },
        # PostgreSQL does not count a conflicting row whose `WHERE` fails against the command
        # tag / rowcount — a stale or equal-version row is therefore indistinguishable, at this
        # layer, from "nothing happened", which is exactly the idempotent behaviour wanted.
        where=HierarchyNode.source_version < stmt.excluded.source_version,
    )
    # `session.execute` is typed to return the generic `Result`, but an INSERT/UPDATE statement
    # always produces a `CursorResult` at runtime — which is what actually carries `rowcount`.
    result = cast("CursorResult[Any]", await session.execute(stmt))
    return int(result.rowcount or 0)
