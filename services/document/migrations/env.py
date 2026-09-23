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


def grant_purge_sweep_reader(connection: Connection) -> None:
    """Column-level `SELECT` on `assets` for the outbox role — the purge sweep's own due-asset
    discovery and the orphan pass's row-existence check (ADR-0009 §8, §9 addendum), the same
    shape as `services/approval/migrations/env.py`'s `grant_sla_sweep_reader`.

    Four columns, named exactly: `organization_id`, `id`, `deleted_at`, `purged_at`. Nothing
    from `storage_key`, `original_filename` or `uploaded_by_user_id` — a bypass role that can
    find which assets are due for purging, or whether a listed bucket key has any row at all,
    must still not be able to read what a merchant uploaded or who uploaded it, and neither
    discovery query needs to.

    `REVOKE` before `GRANT` and run after **every** alembic run, upgrade or downgrade alike —
    the same idempotent shape approval and scheduler use, and for the same reason: a
    `GRANT`-only version would leave the privilege in place forever once issued, including
    across a downgrade that drops the table.
    """
    reader = cfg.db_outbox_role
    if not reader:
        return
    if not connection.execute(
        text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": reader}
    ).scalar():
        return
    if connection.execute(text("SELECT to_regclass(:name)"), {"name": f"{SCHEMA}.assets"}).scalar():
        connection.execute(text(f'REVOKE ALL ON "{SCHEMA}"."assets" FROM "{reader}"'))
        connection.execute(
            text(
                "GRANT SELECT (organization_id, id, deleted_at, purged_at) "
                f'ON "{SCHEMA}"."assets" TO "{reader}"'
            )
        )
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
    grant_purge_sweep_reader(connection)


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
