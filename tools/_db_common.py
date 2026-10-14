"""Database helpers the operator tools share.

Imported as a sibling (`from _db_common import ...`): every tool runs as a script with `tools/`
as `sys.path[0]`, on a laptop and on the box (`-v tools:/opt/tools`). Each import below is local
to its function, so a tool that never touches the database does not pay for SQLAlchemy.
"""

from __future__ import annotations

import uuid


def database_label(dsn: str) -> str:
    """Where a tool is pointed, without the credentials: `host:port/database`."""
    from sqlalchemy.engine import make_url

    url = make_url(dsn)
    return f"{url.host}:{url.port}/{url.database}"


async def all_organization_ids() -> list[uuid.UUID]:
    """Every live organization, read on the directory (BYPASSRLS, SELECT-only) engine.

    `organizations` carries row-level security keyed on its own `id`, so no tenant-bound
    session can enumerate more than the one it is bound to — discovering the fleet is a
    platform read by construction. It does not need `admin_dsn` (BYPASSRLS *and* owner of
    every schema) for one column of one table: `directory_dsn` is the SELECT-only role built
    for exactly this shape. The engine is opened and disposed for this one read; every write
    that follows reconnects through the NOBYPASSRLS app role, one organization at a time.

    Resolves `app.config` / `app.models` from `sys.path`, so the caller must already have put
    the account service on it (both reconcile tools do, in `main`).
    """
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.config import settings  # type: ignore[import-not-found]
    from app.models import Organization  # type: ignore[import-not-found]

    cfg = settings()
    engine = create_async_engine(cfg.directory_dsn)
    try:
        maker = async_sessionmaker(engine, expire_on_commit=False)
        async with maker() as session:
            rows = await session.execute(
                select(Organization.id)
                .where(Organization.deleted_at.is_(None))
                .order_by(Organization.created_at)
            )
            return [row[0] for row in rows.all()]
    finally:
        await engine.dispose()
