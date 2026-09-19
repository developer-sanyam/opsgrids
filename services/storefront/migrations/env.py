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


def grant_directory_reader(connection: Connection) -> None:
    """Cross-tenant SELECT for the anonymous slug lookup, on **one table**.

    `apply_schema_grants` also grants a directory reader, but its table list is account-service's
    identity tables and its role is `opsgrids_directory`; storefront's reader is its own role on
    its own table, so it is granted here explicitly — the `services/product` pattern verbatim.

    `selectors.resolve_public_store` is the entire surface of this role: slug in, two ids and a
    status out. **SELECT only** — no INSERT, UPDATE or DELETE ever reaches it, so a bug on the
    one path in this platform that runs before a tenant is bound cannot write anything. A bypass
    buys discovery only (ADR-0002 §2), and each bypass reader is confined to its own service's
    named tables, which is why this is not `opsgrids_directory`.

    Runs here rather than in the bootstrap SQL because the table does not exist when that runs,
    and re-applies on every migration for the same reason every other grant in this file does —
    a step somebody must remember is a step somebody eventually forgets. Guarded by
    `to_regclass`, so a downgrade that has dropped the table is a no-op rather than an error.
    """
    reader = cfg.db_directory_role
    if not reader:
        return
    if not connection.execute(
        text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": reader}
    ).scalar():
        return
    connection.execute(text(f'GRANT USAGE ON SCHEMA "{SCHEMA}" TO "{reader}"'))
    if connection.execute(
        text("SELECT to_regclass(:name)"), {"name": f"{SCHEMA}.storefronts"}
    ).scalar():
        connection.execute(text(f'GRANT SELECT ON "{SCHEMA}"."storefronts" TO "{reader}"'))
    connection.commit()


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
    grant_directory_reader(connection)


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
