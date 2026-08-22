"""Give the seeded user a password. Run **inside `services/auth`** as a subprocess.

Separate from the account seeder because credentials live in a different service with a
different schema — which is the whole point of the split, and the reason the end-to-end suite
exists at all. Nothing here reaches across a schema boundary.

Usage: `_seed_auth.py <service_dir> <user_id> <password> [keep]`

The first call clears the auth tables for a clean slate. A later call that must not wipe the
credentials already seeded — the invitee who accepted over HTTP still needs the admin's login to
work — passes `keep`, and this leaves existing rows untouched.
"""

from __future__ import annotations

import asyncio
import sys
import uuid
from pathlib import Path

SERVICE_DIR = Path(sys.argv[1]).resolve()
USER_ID = uuid.UUID(sys.argv[2])
PASSWORD = sys.argv[3]
KEEP_EXISTING = len(sys.argv) > 4 and sys.argv[4] == "keep"

sys.path.insert(0, str(SERVICE_DIR))

from app import services  # noqa: E402
from app.config import settings  # noqa: E402
from app.models import SCHEMA, Base  # noqa: E402


async def main() -> None:
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    cfg = settings()
    engine = create_async_engine(
        cfg.admin_dsn,
        connect_args={"server_settings": {"search_path": f"{SCHEMA},public"}},
    )
    if not KEEP_EXISTING:
        tables = ", ".join(f'"{SCHEMA}"."{t.name}"' for t in reversed(Base.metadata.sorted_tables))
        async with engine.begin() as conn:
            await conn.execute(text(f"TRUNCATE {tables} CASCADE"))

    async with async_sessionmaker(engine, expire_on_commit=False)() as session, session.begin():
        await services.set_password(session, USER_ID, PASSWORD, cfg=cfg)

    await engine.dispose()


asyncio.run(main())
