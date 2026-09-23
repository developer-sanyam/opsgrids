"""Alembic environment — async, schema per service.

The schema is created before the first migration runs, so a fresh database needs no
manual DDL. `version_table_schema` keeps this service's alembic_version inside its own
schema rather than a shared public one.

`grant_directory_reader` gives `opsgrids_billing_directory` cross-tenant SELECT on
`credit_ledger` — the same shape console's `grant_directory_reader` uses for its own bypass
role — run here rather than in the bootstrap SQL because the table does not exist when that
runs, and a step somebody must remember is a step somebody eventually forgets.
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

#: Billing's append-only / frozen tables, enforced by table privilege rather than by convention
#: (ADR-0000 §9). `apply_schema_grants` re-GRANTs SELECT/INSERT/UPDATE/DELETE on every run and
#: only auto-revokes `audit_logs`, so these revokes must be re-applied *after* it every time —
#: exactly as `audit_logs` is revoked at the end of `apply_schema_grants` itself. A revoke left
#: only in a migration would be silently clobbered by the grants on the next `alembic upgrade`.
#:   - credit_ledger, invoice_lines: fully append-only (a correction is a new row).
#:   - invoices: DELETE revoked; UPDATE kept for the finalized->void move, which the freeze
#:     trigger gates so the row still cannot be altered in any other way.
_REVOKE_UPDATE_DELETE = ("credit_ledger", "invoice_lines")
_REVOKE_DELETE_ONLY = ("invoices",)

#: The staff credit-ledger reader. SELECT only — no write ever reaches this role, so a bug in
#: the read path cannot write across tenants (mirrors console's `DIRECTORY_READER`).
DIRECTORY_READER = "opsgrids_billing_directory"
DIRECTORY_TABLES = ("credit_ledger",)

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
    _enforce_append_only(connection)
    _grant_directory_reader(connection)


def _grant_directory_reader(connection: Connection) -> None:
    """Cross-tenant SELECT on `credit_ledger` for the staff credit-ledger route.

    Guarded on the role existing, so a database that has not run the bootstrap SQL's role
    creation still migrates cleanly (mirrors console's `grant_directory_reader`).
    """
    if not connection.execute(
        text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": DIRECTORY_READER}
    ).scalar():
        return
    connection.execute(text(f'GRANT USAGE ON SCHEMA "{SCHEMA}" TO "{DIRECTORY_READER}"'))
    for table in DIRECTORY_TABLES:
        if connection.execute(
            text("SELECT to_regclass(:name)"), {"name": f"{SCHEMA}.{table}"}
        ).scalar():
            connection.execute(
                text(f'GRANT SELECT ON "{SCHEMA}"."{table}" TO "{DIRECTORY_READER}"')
            )
    connection.commit()


def _enforce_append_only(connection: Connection) -> None:
    """Re-revoke the append-only privileges `apply_schema_grants` just re-granted.

    Runs after grants, and guarded by table existence so it is a no-op at `downgrade base` when
    the tables are gone. This is what actually keeps the ledger append-only in a live database;
    the RLS migration also revokes them so a `downgrade`/`upgrade` of that revision restores the
    right state, but this is the durable enforcement (see the module note above)."""
    role = cfg.db_app_role
    if not role:
        return
    for table in _REVOKE_UPDATE_DELETE:
        if connection.execute(
            text("SELECT to_regclass(:name)"), {"name": f"{SCHEMA}.{table}"}
        ).scalar():
            connection.execute(text(f'REVOKE UPDATE, DELETE ON "{SCHEMA}"."{table}" FROM "{role}"'))
    for table in _REVOKE_DELETE_ONLY:
        if connection.execute(
            text("SELECT to_regclass(:name)"), {"name": f"{SCHEMA}.{table}"}
        ).scalar():
            connection.execute(text(f'REVOKE DELETE ON "{SCHEMA}"."{table}" FROM "{role}"'))
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
