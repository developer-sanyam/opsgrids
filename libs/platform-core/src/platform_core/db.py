"""Engine, sessions, and the tenant guard.

Three things here are load-bearing and easy to get wrong.

**`search_path` is per connection; the tenant id must not be.** The pool hands the same
connection to the next request, so a tenant GUC set in `connect_args` leaks across
tenants. `set_config(..., true)` inside the transaction is the only safe form — it is
cleared automatically on commit or rollback.

**`get_session` never commits, and opens no transaction.** It is what the pre-organization
paths — sign-in, invitation acceptance — take, and what the test fixtures override. A
dependency that commits for you takes away the property that a state change, its audit
row and its outbox row share one transaction.

**`get_tenant_session` is the chokepoint** (ADR-0002 §1). It opens the one transaction a
request with a principal runs in and binds it to the caller's organization as the first
statement. The GUC is transaction-local and does not survive into a later transaction on
the same session, so it cannot be set once at request entry — it has to be set by whatever
opens the transaction. Routes with a principal take it and open nothing themselves; a
route that forgets is caught by the route audit in each service's tests.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from typing import Annotated, Any

from fastapi import Depends, Request
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from platform_core.auth import CurrentPrincipal
from platform_core.config import ServiceSettings
from platform_core.logging import get_logger

log = get_logger("db")

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None
_directory_engine: AsyncEngine | None = None
_directory_sessionmaker: async_sessionmaker[AsyncSession] | None = None
_outbox_engine: AsyncEngine | None = None
_outbox_sessionmaker: async_sessionmaker[AsyncSession] | None = None

#: Transaction-local GUC read by every row-level security policy.
TENANT_GUC = "app.organization_id"


def apply_schema_grants(
    connection: Any, schema: str, app_role: str | None, *, outbox_role: str | None = None
) -> None:
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

    # The directory reader: cross-tenant SELECT on the identity tables only. Granted here for
    # the same reason as everything else in this function — the tables do not exist when the
    # bootstrap runs, and a step somebody must remember is a step somebody forgets.
    directory = "opsgrids_directory"
    if connection.execute(
        text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": directory}
    ).scalar():
        connection.execute(text(f'GRANT USAGE ON SCHEMA "{schema}" TO "{directory}"'))
        for table in (
            "users",
            "memberships",
            "membership_roles",
            "roles",
            "role_permissions",
            "permissions",
            "organizations",
            # `GET /internal/v1/domains/{domain}` — the verified-domain lookup single sign-on
            # starts from. Cross-tenant for the same reason the user lookups are: it is asked
            # in order to *discover* the tenant, so there is none to bind while asking. SELECT
            # only; the verification write stays on the tenant-bound application role.
            "org_domains",
            # The public `POST /api/v1/invitations/accept` path resolves an invitation's
            # organization on the bypass session before any tenant is bound, then does every
            # write under a tenant-scoped transaction. This is the one row of the one table
            # that read touches — SELECT only, no write ever reaches this role (ADR-0002,
            # "What breaks", path 4: bypass for the lookup only, then narrow).
            "invitations",
        ):
            if connection.execute(
                text("SELECT to_regclass(:name)"), {"name": f"{schema}.{table}"}
            ).scalar():
                connection.execute(text(f'GRANT SELECT ON "{schema}"."{table}" TO "{directory}"'))

    # The relay and the outbox-lag probe: cross-tenant, and confined to the one table whose
    # rows they mark. SELECT and UPDATE are what `FOR UPDATE SKIP LOCKED` and the
    # `published_at`/`attempts` writes need; INSERT is withheld because only a request
    # transaction stages events, DELETE because the outbox is the record of what was sent.
    if (
        outbox_role
        and connection.execute(
            text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": outbox_role}
        ).scalar()
    ):
        connection.execute(text(f'GRANT USAGE ON SCHEMA "{schema}" TO "{outbox_role}"'))
        if connection.execute(
            text("SELECT to_regclass(:name)"), {"name": f"{schema}.outbox"}
        ).scalar():
            connection.execute(
                text(f'GRANT SELECT, UPDATE ON "{schema}"."outbox" TO "{outbox_role}"')
            )

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


async def assert_app_role(engine: AsyncEngine, *, environment: str) -> None:
    """Refuse to serve as a role that row-level security cannot bind.

    A superuser, or any role with BYPASSRLS, ignores every policy unconditionally — not
    even `FORCE ROW LEVEL SECURITY` applies to it. Policies written against such a
    connection look enforced in dev, CI, review and production alike, and are not
    (ADR-0002 finding 2). Outside `local` that is fatal. In `local` it is a warning,
    because a developer pointed at the wrong DSN should find out without being locked out
    of their own machine.
    """
    probe = text(
        "SELECT rolname, rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user"
    )
    async with engine.connect() as conn:
        role, superuser, bypassrls = (await conn.execute(probe)).one()

    if not (superuser or bypassrls):
        return
    if environment != "local":
        raise RuntimeError(
            f"refusing to start as database role {role!r}: rolsuper={superuser}, "
            f"rolbypassrls={bypassrls}. Row-level security would be silently inert; "
            "connect as the service's own NOBYPASSRLS role"
        )
    log.warning(
        "db.role.bypasses_rls",
        role=role,
        superuser=superuser,
        bypassrls=bypassrls,
        environment=environment,
    )


async def assert_outbox_role(engine: AsyncEngine, *, environment: str) -> None:
    """Refuse to run a relay as a role row-level security still binds.

    The mirror image of `assert_app_role`. The relay's query exists to discover which
    organizations have work, so it binds no tenant — and a NOBYPASSRLS role with no tenant
    bound selects zero rows, logs nothing and loops while the outbox fills (ADR-0002,
    "Relay stall"). That is the platform's quietest failure, so outside `local` a relay
    handed such a connection must die loudly at startup rather than stall silently. In
    `local` it is a warning, for the same reason `assert_app_role` warns there: a developer
    who has not split the roles should find out without being locked out.
    """
    probe = text(
        "SELECT rolname, rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user"
    )
    async with engine.connect() as conn:
        role, superuser, bypassrls = (await conn.execute(probe)).one()

    if superuser or bypassrls:
        return
    if environment != "local":
        raise RuntimeError(
            f"refusing to run the outbox relay as database role {role!r}: it has neither "
            "BYPASSRLS nor superuser, so under row-level security its drain query matches "
            "zero rows and the outbox fills silently. Configure db_outbox_role "
            "(opsgrids_outbox) and its grants"
        )
    log.warning(
        "outbox.role.rls_bound",
        role=role,
        environment=environment,
        hint="the relay would stall silently under row-level security; split the outbox role",
    )


async def set_tenant(session: AsyncSession, organization_id: uuid.UUID) -> None:
    """Bind this transaction to one organization, for row-level security.

    `true` makes the setting transaction-local. Without it the value survives on the
    pooled connection and the next request inherits someone else's tenancy.
    """
    await session.execute(
        text(f"SELECT set_config('{TENANT_GUC}', :org, true)"),
        {"org": str(organization_id)},
    )


def init_directory_engine(settings: ServiceSettings) -> None:
    """A second, deliberately tiny engine for the cross-tenant directory reads.

    Separate from the application engine because it connects as a different role — BYPASSRLS
    so it can resolve a person before an organization is known, and SELECT-only on a named
    handful of tables so a bug in that module cannot write anything.

    A small pool on purpose: three read-only endpoints do not need the application's.
    """
    global _directory_engine, _directory_sessionmaker
    if _directory_engine is not None or not settings.db_directory_role:
        return

    _directory_engine = create_async_engine(
        settings.directory_dsn,
        pool_size=3,
        max_overflow=2,
        pool_pre_ping=True,
        connect_args={
            "server_settings": {
                "search_path": f"{settings.db_schema},public",
                "application_name": f"{settings.service_name}-directory",
            }
        },
    )
    _directory_sessionmaker = async_sessionmaker(
        _directory_engine, expire_on_commit=False, autoflush=False, class_=AsyncSession
    )


async def dispose_directory_engine() -> None:
    global _directory_engine, _directory_sessionmaker
    if _directory_engine is not None:
        await _directory_engine.dispose()
    _directory_engine = None
    _directory_sessionmaker = None


async def get_directory_session() -> AsyncIterator[AsyncSession]:
    """Read-only, cross-tenant, and used by nothing except the internal directory routes.

    Falls back to the ordinary session when no directory role is configured, so a deployment
    that has not split the roles still works — it simply gets no bypass, which is the safe
    direction to be wrong in.
    """
    if _directory_sessionmaker is None:
        async for session in get_session():
            yield session
        return

    async with _directory_sessionmaker() as session:
        yield session


def init_outbox_engine(settings: ServiceSettings) -> None:
    """A third engine, for the two things that must see every tenant's outbox rows.

    The relay and the `/health/outbox` probe connect as a role with BYPASSRLS, because the
    relay's query exists to discover which organizations have work — there is no tenant to
    bind before it runs. Not the admin role: that one owns the schema and can write anything,
    and a relay bug must be able to touch nothing but `outbox`. Not the app role: under
    row-level security with no tenant bound it selects zero rows, logs nothing and loops
    while the outbox fills (ADR-0002, "Relay stall").

    When no outbox role is configured this opens nothing, and `get_outbox_sessionmaker` and
    `get_outbox_engine` fall back to the application engine. **That fallback is the
    silent-stall configuration.** It exists only so a deployment that has not split its roles
    still starts; it is logged at warning level here so it is never invisible, because the
    relay and probe built on it see no rows once the policies are live.

    A small pool on purpose: one polling loop and one probe do not need the application's.
    """
    global _outbox_engine, _outbox_sessionmaker
    if _outbox_engine is not None:
        return
    if not settings.db_outbox_role:
        log.warning(
            "outbox.role.not_split",
            service=settings.service_name,
            hint="db_outbox_role is unset; the relay and /health/outbox run as the app role "
            "and see no rows under row-level security",
        )
        return

    _outbox_engine = create_async_engine(
        settings.outbox_dsn,
        pool_size=2,
        max_overflow=2,
        pool_pre_ping=True,
        connect_args={
            "server_settings": {
                "search_path": f"{settings.db_schema},public",
                "application_name": f"{settings.service_name}-outbox",
            }
        },
    )
    _outbox_sessionmaker = async_sessionmaker(
        _outbox_engine, expire_on_commit=False, autoflush=False, class_=AsyncSession
    )


def get_outbox_engine() -> AsyncEngine:
    """The engine `/health/outbox` counts through. Falls back to the app engine when unsplit."""
    if _outbox_engine is None:
        return get_engine()
    return _outbox_engine


def get_outbox_sessionmaker() -> async_sessionmaker[AsyncSession]:
    """What `OutboxRelay` drains through.

    Falls back to the application sessionmaker when no outbox role is configured — see
    `init_outbox_engine` for why that fallback is the stall, and is tolerated only so an
    un-split deployment starts.
    """
    if _outbox_sessionmaker is None:
        return get_sessionmaker()
    return _outbox_sessionmaker


async def dispose_outbox_engine() -> None:
    global _outbox_engine, _outbox_sessionmaker
    if _outbox_engine is not None:
        await _outbox_engine.dispose()
    _outbox_engine = None
    _outbox_sessionmaker = None


async def assert_tenant(session: AsyncSession, organization_id: uuid.UUID) -> None:
    """Raise unless this transaction's tenant GUC names `organization_id`.

    The ADR-0002 §4 probe. Under RLS an unset or wrong tenant does not error — it returns
    zero rows, and the application already has friendly messages that dress that up as
    ordinary empty state. One round trip here turns the quietest failure in the platform
    into an immediate one. `current_setting(..., true)` returns NULL when the GUC was never
    set on the connection and '' once an earlier transaction's value has expired; both are
    a mismatch.
    """
    current = (
        await session.execute(text(f"SELECT current_setting('{TENANT_GUC}', true)"))
    ).scalar_one()
    if current != str(organization_id):
        raise RuntimeError(
            f"tenant GUC {TENANT_GUC} is {current!r} inside a request for organization "
            f"{organization_id}: the transaction is not bound to the caller's tenant"
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


async def get_tenant_session(
    principal: CurrentPrincipal,
    session: Annotated[AsyncSession, Depends(get_session)],
    request: Request,
) -> AsyncIterator[AsyncSession]:
    """The tenancy chokepoint. **Every tenant-scoped route goes through this.**

    Opens the transaction and binds it to the caller's organization as its *first*
    statement, then yields. Commits on a clean return, rolls back on an exception.

    It has to be the transaction opener rather than something that runs before one. A
    dependency that merely set the GUC and yielded would autobegin a transaction, and the
    route's own `async with session.begin()` would then raise "a transaction is already
    begun" — confirmed against SQLAlchemy rather than assumed. So routes do not open their
    own transaction; this does it for them.

    The GUC is transaction-local, and that is load-bearing twice over. It stops a pooled
    connection carrying one tenant's id into the next request that forgets to set it — the
    failure that returns HTTP 200 with another customer's rows and no error anywhere. And it
    means the value does **not** survive into a later transaction on the same session, so a
    single call at request entry would not be enough; it must be set per transaction, which
    is exactly what this does.

    Today this is a no-op against a database with no policies: nothing reads the GUC yet.
    That is deliberate — it lands and is proven on its own, before any policy exists to
    confuse a failure with.

    Outside production, one extra round trip reads the GUC back and raises if it is not
    the principal's organization. Under RLS a missing tenant yields zero rows and the
    application would report that as normal empty state (ADR-0002 §4); the probe makes it
    loud instead.
    """
    settings: ServiceSettings = request.app.state.settings
    async with session.begin():
        await set_tenant(session, principal.organization_id)
        if not settings.is_production:
            await assert_tenant(session, principal.organization_id)
        yield session


@asynccontextmanager
async def session_scope(
    organization_id: uuid.UUID | None = None,
) -> AsyncIterator[AsyncSession]:
    """Transaction for workers, relays and scripts. Commits on a clean exit.

    Pass `organization_id` when the work belongs to one tenant. Anything that deliberately
    passes nothing must run as a role with BYPASSRLS — the relay has its own, `opsgrids_outbox`,
    reached through `get_outbox_sessionmaker` rather than this — see CLAUDE.md, because
    getting this wrong stops event processing silently.
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
