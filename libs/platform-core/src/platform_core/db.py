"""Engine, sessions, and the tenant guard.

Two things here are load-bearing and easy to get wrong.

**`search_path` is per connection; the tenant id must not be.** The pool hands the same
connection to the next request, so a tenant GUC set in `connect_args` leaks across
tenants. `set_config(..., true)` inside the transaction is the only safe form — it is
cleared automatically on commit or rollback.

**`get_session` never commits.** Service functions open `async with session.begin()`
themselves, which is what lets a state change, its audit row and its outbox row share one
transaction. A dependency that commits for you takes that away.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from typing import Any

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from platform_core.config import ServiceSettings

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None

#: Transaction-local GUC read by every row-level security policy.
TENANT_GUC = "app.organization_id"


def apply_schema_grants(connection: Any, schema: str, app_role: str | None) -> None:
    """Give this service's app role exactly what it needs on its own schema.

    Called from every service's `migrations/env.py` after the migrations run, rather than
    written into the bootstrap SQL, for two reasons: the schema and its tables do not exist
    when the bootstrap runs, and a per-service step somebody has to remember is a step
    somebody eventually forgets — which is exactly how Redis ended up uninitialised.

    Three things are deliberately withheld:

    - **TRUNCATE**, because it is not filtered by row-level security. It is all-or-nothing,
      so an app role holding it can empty a table across every tenant at once.
    - **UPDATE and DELETE on `audit_logs`**, because a trail the application can rewrite is
      not a trail. Table privileges are the right tool for append-only; RLS is not.
    - Any privilege at all on **another service's schema**. That omission is the point.
    """
    if not app_role:
        return

    for statement in (
        f'GRANT USAGE ON SCHEMA "{schema}" TO "{app_role}"',
        f'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA "{schema}" TO "{app_role}"',
        f'GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA "{schema}" TO "{app_role}"',
        # Tables a *future* migration creates, so grants never drift behind the schema.
        f'ALTER DEFAULT PRIVILEGES IN SCHEMA "{schema}" '
        f'GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO "{app_role}"',
        f'ALTER DEFAULT PRIVILEGES IN SCHEMA "{schema}" '
        f'GRANT USAGE, SELECT ON SEQUENCES TO "{app_role}"',
    ):
        connection.execute(text(statement))

    # Append-only, enforced by privilege rather than by convention.
    if connection.execute(
        text("SELECT to_regclass(:name)"), {"name": f"{schema}.audit_logs"}
    ).scalar():
        connection.execute(
            text(f'REVOKE UPDATE, DELETE ON "{schema}"."audit_logs" FROM "{app_role}"')
        )

    connection.commit()


def init_engine(settings: ServiceSettings) -> AsyncEngine:
    """Create the process-wide engine. Called once, from the app lifespan."""
    global _engine, _sessionmaker
    if _engine is not None:
        return _engine

    _engine = create_async_engine(
        settings.app_dsn,
        echo=settings.db_echo,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_pre_ping=True,
        connect_args={
            "server_settings": {
                # Unqualified DDL and DML land in this service's own schema.
                "search_path": f"{settings.db_schema},public",
                "application_name": settings.service_name,
            }
        },
    )

    # Belt and braces: if a non-local GUC is ever set by mistake, it must not survive
    # the connection going back to the pool.
    @event.listens_for(_engine.sync_engine, "reset")
    def _clear_tenant(dbapi_conn, _record, _reset_state) -> None:  # type: ignore[no-untyped-def]
        # Suppressed deliberately: this runs while a connection is being returned, often
        # because something already failed. Raising here would replace the real error
        # with this one and could poison the pool.
        with suppress(Exception):
            cur = dbapi_conn.cursor()
            cur.execute(f"RESET {TENANT_GUC}")
            cur.close()

    _sessionmaker = async_sessionmaker(
        _engine, expire_on_commit=False, autoflush=False, class_=AsyncSession
    )
    return _engine


def get_engine() -> AsyncEngine:
    if _engine is None:
        raise RuntimeError("init_engine() has not been called — is this inside the app lifespan?")
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    if _sessionmaker is None:
        raise RuntimeError("init_engine() has not been called")
    return _sessionmaker


async def dispose_engine() -> None:
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _sessionmaker = None


async def set_tenant(session: AsyncSession, organization_id: uuid.UUID) -> None:
    """Bind this transaction to one organization, for row-level security.

    `true` makes the setting transaction-local. Without it the value survives on the
    pooled connection and the next request inherits someone else's tenancy.
    """
    await session.execute(
        text(f"SELECT set_config('{TENANT_GUC}', :org, true)"),
        {"org": str(organization_id)},
    )


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency. No implicit transaction, and never commits."""
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


@asynccontextmanager
async def session_scope(
    organization_id: uuid.UUID | None = None,
) -> AsyncIterator[AsyncSession]:
    """Transaction for workers, relays and scripts. Commits on a clean exit.

    Pass `organization_id` when the work belongs to one tenant. Relays and fan-out
    consumers deliberately pass nothing and must run as a role with BYPASSRLS — see
    CLAUDE.md, because getting this wrong stops event processing silently.
    """
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session, session.begin():
        if organization_id is not None:
            await set_tenant(session, organization_id)
        yield session


async def get_scoped(
    session: AsyncSession,
    model: type[Any],
    pk: uuid.UUID,
    organization_id: uuid.UUID,
) -> Any:
    """`session.get` with the tenant check that is otherwise forgotten.

    Returns None rather than raising Forbidden on a mismatch: confirming that an id
    exists in *some* organization is itself a disclosure.
    """
    obj: Any = await session.get(model, pk)
    if obj is None:
        return None
    if getattr(obj, "organization_id", None) != organization_id:
        return None
    if getattr(obj, "deleted_at", None) is not None:
        return None
    return obj
