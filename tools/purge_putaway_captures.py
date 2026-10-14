#!/usr/bin/env python3
"""Purge storage put-away captures past their 30-day retention — decision D3 of the warehouse-map
spec (`docs/superpowers/specs/2026-10-05-warehouse-map-putaway-design.md`, §2 and §4.9;
ADR-0028).

A capture is the worker's GPS fix at the moment they scanned a bin (`operations.putaway_captures`).
It is audit evidence for a put-away, kept 30 days and then erased. It is **not** an in-app job, on
purpose: the table is append-only by privilege (`UPDATE` and `DELETE` are revoked from the app role
`opsgrids_operations`), so nothing the service runs could delete a row even if it tried, and the
erasure is a decision a human takes and records.

**Which role, and why it is the one place a tool in this directory uses `admin_dsn`.** Deleting from
an append-only table needs a role that still holds `DELETE` on it, and the only such role is the
migration/owner role `opsgrids_admin` (BYPASSRLS, owner of every schema; ADR-0002 §2). There is no
narrower role to prefer here, unlike `tools/reconcile_permissions.py`, which writes through the app
role. What keeps this contained instead: the statements below are the whole of what it runs, the
default is a read-only dry run (`SET TRANSACTION READ ONLY`, so even a bug cannot delete), and every
delete is bounded by one cutoff taken once at the start.

Usage, from the repository root (point `DATABASE_URL` at the intended database first — in a
worktree that is the isolated `opsgrids_wm`, never the shared `opsgrids`):

    uv run python tools/purge_putaway_captures.py                    # dry run, every organization
    uv run python tools/purge_putaway_captures.py --organization-id <uuid>
    uv run python tools/purge_putaway_captures.py --apply [--organization-id <uuid>]

On the box (RUNBOOK, "Warehouse map and storage put-away"), run it from the `operations` image
exactly as `remote.sh reconcile-permissions` runs its tool, so it gets the service's own settings.

**What it prints: counts only.** One JSON line per organization (`organization_id`, `eligible`,
`with_coordinates`, `deleted`) and a final summary line. It never selects, prints or logs a
coordinate, an accuracy, a worker or a move — the SQL below counts `lat_micro IS NOT NULL` and
never reads the value — and it never prints the connection string, only `host:port/database` so the
operator can see which database it is aimed at. Keep the output with the change record: the purge
writes no audit row of its own (the output is the evidence).

**The cutoff is `created_at`** (the server's clock when the row was written), not `captured_at`
(the device's claim, which a client controls). It is read from the database's own `now()` once, and
every statement then uses that one value, so the run's "eligible" figure is exactly what it deletes:
a row cannot become eligible mid-run, because a new row is never older than the cutoff. The window
is a constant, not a flag: a purge that can be pointed at "0 days" is a purge that can erase
evidence by typo. Deletion is in batches of 5,000, each its own transaction, so a long backlog
neither holds a lock nor one huge transaction; an interrupted `--apply` is safe to simply re-run.

A purged capture is gone for good: the reveal endpoint answers 404 for its move ("capture expired")
and the put-away itself (the move, the bin, the audit rows) is untouched. Backups taken before the
purge still hold the rows until their own retention expires (docs/engage-erasure-and-unsubscribe.md
says the same of every delete in this database).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import uuid
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from _db_common import database_label
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

ROOT = Path(__file__).resolve().parent.parent

#: D3: the retention window. Deliberately not a command-line option (see the module docstring).
RETENTION_DAYS = 30
#: Rows deleted per transaction under `--apply`.
BATCH_SIZE = 5_000
TABLE = "putaway_captures"

_IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]*$")


def _qualified(schema: str) -> str:
    """`"<schema>".putaway_captures`. The schema comes from the service's own settings, not from
    a user, but it is spliced into SQL text, so it is checked rather than trusted."""
    if not _IDENTIFIER.match(schema):
        raise ValueError(f"refusing schema name {schema!r}: not a plain lower-case identifier")
    return f'"{schema}".{TABLE}'


def _org_clause(organization_id: uuid.UUID | None) -> str:
    return "AND organization_id = :org" if organization_id else ""


def _params(organization_id: uuid.UUID | None, **extra: Any) -> dict[str, Any]:
    params = dict(extra)
    if organization_id:
        params["org"] = organization_id
    return params


async def _cutoff(conn: Any, retention_days: int) -> datetime:
    row = await conn.execute(
        text("SELECT now() - make_interval(days => :days)"), {"days": retention_days}
    )
    cutoff: datetime = row.scalar_one()
    return cutoff


async def _plan(
    conn: Any, table: str, cutoff: datetime, organization_id: uuid.UUID | None
) -> tuple[list[tuple[uuid.UUID, int, int]], int]:
    """(per-organization `(id, eligible, with_coordinates)`, how many rows are retained).

    `lat_micro IS NOT NULL` is a count of fixes, not a read of one: no statement here puts a
    coordinate in a select list."""
    clause = _org_clause(organization_id)
    eligible = await conn.execute(
        text(
            f"SELECT organization_id, count(*) AS eligible, "  # noqa: S608
            f"count(*) FILTER (WHERE lat_micro IS NOT NULL) AS with_coordinates "
            f"FROM {table} WHERE created_at < :cutoff {clause} "
            f"GROUP BY organization_id ORDER BY organization_id"
        ),
        _params(organization_id, cutoff=cutoff),
    )
    retained = await conn.execute(
        text(f"SELECT count(*) FROM {table} WHERE created_at >= :cutoff {clause}"),  # noqa: S608
        _params(organization_id, cutoff=cutoff),
    )
    return [(r[0], r[1], r[2]) for r in eligible.all()], retained.scalar_one()


async def _delete_batch(
    conn: Any,
    table: str,
    cutoff: datetime,
    organization_id: uuid.UUID | None,
    batch_size: int,
) -> list[uuid.UUID]:
    """Delete up to `batch_size` eligible rows; return the organization id of each one deleted
    (and nothing else about it)."""
    clause = _org_clause(organization_id)
    result = await conn.execute(
        text(
            f"DELETE FROM {table} WHERE id IN ("  # noqa: S608
            f"SELECT id FROM {table} WHERE created_at < :cutoff {clause} "
            f"ORDER BY created_at, id LIMIT :batch) "
            f"RETURNING organization_id"
        ),
        _params(organization_id, cutoff=cutoff, batch=batch_size),
    )
    return [r[0] for r in result.all()]


async def run(
    engine: AsyncEngine,
    *,
    schema: str,
    apply: bool,
    organization_id: uuid.UUID | None = None,
    retention_days: int = RETENTION_DAYS,
    batch_size: int = BATCH_SIZE,
    database: str = "",
) -> dict[str, Any]:
    """Plan, optionally delete, print the counts, and return the summary line."""
    if batch_size < 1:
        raise ValueError("batch_size must be at least 1")
    table = _qualified(schema)

    try:
        # The plan is always read-only. A dry run is exactly this and nothing more.
        async with engine.begin() as conn:
            await conn.execute(text("SET TRANSACTION READ ONLY"))
            cutoff = await _cutoff(conn, retention_days)
            plan, retained = await _plan(conn, table, cutoff, organization_id)

        deleted: Counter[uuid.UUID] = Counter()
        if apply:
            while True:
                async with engine.begin() as conn:
                    gone = await _delete_batch(conn, table, cutoff, organization_id, batch_size)
                deleted.update(gone)
                if not gone:
                    break
    finally:
        await engine.dispose()

    for org, eligible, with_coordinates in plan:
        print(
            json.dumps(
                {
                    "organization_id": str(org),
                    "eligible": eligible,
                    "with_coordinates": with_coordinates,
                    "deleted": deleted[org],
                }
            )
        )
    summary: dict[str, Any] = {
        "mode": "apply" if apply else "dry-run",
        "database": database,
        "retention_days": retention_days,
        "cutoff": cutoff.isoformat(),
        "organization_filter": str(organization_id) if organization_id else None,
        "organizations": len(plan),
        "eligible": sum(p[1] for p in plan),
        "with_coordinates": sum(p[2] for p in plan),
        "retained": retained,
        "deleted": sum(deleted.values()),
    }
    print(json.dumps(summary))
    if not apply:
        print("dry run only: nothing was deleted. Re-run with --apply to delete the eligible rows.")
    elif summary["deleted"] != summary["eligible"]:
        print(
            "note: deleted differs from eligible. The table changed under the run; re-run the "
            "dry run to see what is left.",
            file=sys.stderr,
        )
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            f"Delete storage put-away captures older than {RETENTION_DAYS} days (spec D3). "
            "Dry run unless --apply. Prints counts only, never a coordinate."
        )
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="actually delete the eligible captures (the default is a read-only dry run)",
    )
    parser.add_argument(
        "--organization-id",
        type=uuid.UUID,
        metavar="UUID",
        help="limit the purge to one organization (default: every organization)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    # The operations service directory becomes the import root, exactly as its own processes run:
    # every service exposes a top-level `app`, so one process holds exactly one.
    sys.path.insert(0, str(ROOT / "services" / "operations"))
    from app.config import settings  # type: ignore[import-not-found]

    cfg = settings()
    dsn = cfg.admin_dsn
    asyncio.run(
        run(
            create_async_engine(dsn),
            schema=cfg.db_schema,
            apply=args.apply,
            organization_id=args.organization_id,
            database=database_label(dsn),
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
