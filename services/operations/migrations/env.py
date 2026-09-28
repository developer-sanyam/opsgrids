"""Alembic environment — async, schema per service.

The schema is created before the first migration runs, so a fresh database needs no
manual DDL. `version_table_schema` keeps this service's alembic_version inside its own
schema rather than a shared public one.
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from app.config import settings
from app.models import SCHEMA, Base
from platform_core.db import apply_schema_grants

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

cfg = settings()
# Migrations run as the admin role: it owns the schema, and DDL is not subject to
# row-level security. The API connects as a different, NOBYPASSRLS role.
config.set_main_option("sqlalchemy.url", cfg.admin_dsn)
target_metadata = Base.metadata


def include_object(obj, name, type_, reflected, compare_to) -> bool:  # type: ignore[no-untyped-def]
    """Manage only this service's own schema.

    Anything else — another service's tables, or PostGIS's public.spatial_ref_sys, which
    reflects with schema=None — must be invisible, or autogenerate proposes dropping it.
    """
    if type_ == "table":
        return bool(obj.schema == SCHEMA)
    return True


def do_run_migrations(connection: Connection) -> None:
    connection.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{SCHEMA}"'))
    connection.commit()
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        include_schemas=True,
        include_object=include_object,
        version_table_schema=SCHEMA,
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()

    # After the schema is up to date, not before: the grants name tables the migrations just
    # created. Doing it here rather than as a per-service step means a new service cannot ship
    # without them -- which is exactly what happened the first time this was left out.
    apply_schema_grants(connection, SCHEMA, cfg.db_app_role, outbox_role=cfg.db_outbox_role)

    # The stock ledger is append-only, enforced by a table privilege rather than a policy.
    # `apply_schema_grants` above re-grants UPDATE/DELETE on *every* table and only re-revokes
    # `audit_logs`, so a revoke that lived solely in a migration would be undone on each later
    # `alembic upgrade`. Re-applying it here, right after the grants, in exactly the shape
    # `apply_schema_grants` uses for `audit_logs`, is what makes append-only hold across grant
    # re-application — the pattern `console`/`metering` use for their own append-only tables.
    # `stock_movements` is the ledger's name before ADR-0014's migration, `inventory_ledger` and
    # its `inventory_moves` headers after; each is guarded on existing, so a run at any revision —
    # upgrade or downgrade — revokes exactly what is there.
    for table in APPEND_ONLY_TABLES:
        if cfg.db_app_role and _exists(connection, table):
            connection.execute(
                text(f'REVOKE UPDATE, DELETE ON {SCHEMA}.{table} FROM "{cfg.db_app_role}"')
            )
            connection.commit()
    grant_reconcile_discovery(connection)


#: Append-only by privilege, at whichever revision the schema is at.
APPEND_ONLY_TABLES = (
    "stock_movements",
    "inventory_ledger",
    "inventory_moves",
    "inventory_reservation_releases",
    "order_transitions",
)
#: What the ledger reconciler's discovery reads cross-tenant (`app/cron.py`).
RECONCILE_DISCOVERY_TABLES = ("inventory_balances", "inventory_ledger")


def _exists(connection: Connection, table: str) -> bool:
    return bool(
        connection.execute(
            text("SELECT to_regclass(:name)"), {"name": f"{SCHEMA}.{table}"}
        ).scalar()
    )


def grant_reconcile_discovery(connection: Connection) -> None:
    """Column-level `SELECT (organization_id)` for the outbox role on the ledger and balances.

    The nightly reconciler (`app/cron.py`) has to find out *which organizations* hold stock
    before it can bind a tenant, and as the NOBYPASSRLS app role that question returns nothing —
    a reconciler that checks no one looks exactly like one that found no drift. The same split
    approval's SLA sweep uses: the BYPASSRLS outbox role discovers, the app role does every read
    that matters under the tenant policy. One column, not the table: the bypass buys the list of
    organizations and nothing about their stock.

    `REVOKE` then `GRANT` on every run (upgrade or downgrade), guarded on the table existing, so
    the privilege goes away with the table rather than lingering — `approval`'s env.py explains
    why a GRANT-only version never takes it back.
    """
    reader = cfg.db_outbox_role
    if (
        not reader
        or not connection.execute(
            text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": reader}
        ).scalar()
    ):
        return
    for table in RECONCILE_DISCOVERY_TABLES:
        if _exists(connection, table):
            connection.execute(text(f'REVOKE ALL ON "{SCHEMA}"."{table}" FROM "{reader}"'))
            connection.execute(
                text(f'GRANT SELECT (organization_id) ON "{SCHEMA}"."{table}" TO "{reader}"')
            )
    connection.commit()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_offline() -> None:
    context.configure(
        url=cfg.admin_dsn,
        target_metadata=target_metadata,
        literal_binds=True,
        include_schemas=True,
        include_object=include_object,
        version_table_schema=SCHEMA,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_async_migrations())
