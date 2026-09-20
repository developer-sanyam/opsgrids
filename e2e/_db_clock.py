"""Print PostgreSQL's own `now()`. Run inside any service directory.

Approval measures every SLA instant, every delegation window and every step's `opened_at`
against the database's clock and nothing else (ADR-0006 §8, `approval/app/services.db_now`).
A test that anchors a delegation window or an escalation deadline to *this process's* clock is
therefore comparing two different machines' opinions of "now", and on a container whose clock has
drifted — which has happened on this project — the failure looks like a routing bug rather than
what it is. So the suite reads the same clock the service does, and derives its windows from it.

Prints one ISO-8601 instant. Usage: `_db_clock.py <service_dir>`
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

SERVICE_DIR = Path(sys.argv[1]).resolve()

sys.path.insert(0, str(SERVICE_DIR))

from app.config import settings  # noqa: E402


async def main() -> None:
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(settings().admin_dsn)
    async with engine.connect() as conn:
        now = (await conn.execute(text("SELECT now()"))).scalar_one()
    await engine.dispose()
    print(now.isoformat())


asyncio.run(main())
