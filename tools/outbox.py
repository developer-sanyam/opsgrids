#!/usr/bin/env python3
"""Inspect a service's outbox backlog and requeue rows the relay has given up on.

The relay stops retrying a row once `attempts` reaches its budget (it backs off between
failing passes, so exhausting the budget means a real, sustained problem — every exhaustion
is also an `outbox.abandoned` error in the relay's log). Exhausted rows sit unpublished
forever unless somebody puts them back in play. This is that somebody:

    python tools/outbox.py --service product              # report pending / exhausted counts
    python tools/outbox.py --service product --reset      # re-arm exhausted rows

Connects as `opsgrids_outbox` by default — the role whose whole grant is SELECT and UPDATE on
`outbox`, so this tool cannot touch anything else. **Reset after fixing the cause**: the
relay will simply burn the fresh budget against the same broker if nothing changed.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from platform_core.config import ServiceSettings

DEFAULT_EXHAUSTED_AT = 50  # OutboxRelay's default max_attempts


async def _run(args: argparse.Namespace) -> int:
    settings = ServiceSettings(service_name=args.service, db_schema=args.schema)
    engine = create_async_engine(settings.dsn_as(args.role))
    schema = args.schema

    try:
        async with engine.connect() as conn:
            pending, exhausted = (
                await conn.execute(
                    text(
                        f"SELECT count(*) FILTER (WHERE attempts < :cap), "  # noqa: S608
                        f"count(*) FILTER (WHERE attempts >= :cap) "
                        f'FROM "{schema}".outbox WHERE published_at IS NULL'
                    ),
                    {"cap": args.exhausted_at},
                )
            ).one()
        print(f"{args.service}: {pending} pending, {exhausted} exhausted (>= {args.exhausted_at})")

        if not args.reset:
            return 0
        if not exhausted:
            print("nothing to reset")
            return 0

        async with engine.begin() as conn:
            result = await conn.execute(
                text(
                    f'UPDATE "{schema}".outbox SET attempts = 0 '  # noqa: S608
                    f"WHERE published_at IS NULL AND attempts >= :cap"
                ),
                {"cap": args.exhausted_at},
            )
        print(f"re-armed {result.rowcount} row(s); the relay will pick them up on its next pass")
        return 0
    finally:
        await engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--service", required=True, help="service name, e.g. product")
    parser.add_argument("--schema", help="schema name (defaults to the service name)")
    parser.add_argument("--role", default="opsgrids_outbox", help="database role to connect as")
    parser.add_argument(
        "--exhausted-at",
        type=int,
        default=DEFAULT_EXHAUSTED_AT,
        help="attempts at which the relay stops retrying (default 50)",
    )
    parser.add_argument("--reset", action="store_true", help="re-arm exhausted rows")
    args = parser.parse_args()
    if not args.schema:
        args.schema = args.service
    return asyncio.run(_run(args))


if __name__ == "__main__":
    sys.exit(main())
