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


def grant_unapplied_alarm_reader(connection: Connection) -> None:
    """Column-level `SELECT` on `approval_requests` for the outbox role — the decided-but-never-
    applied alarm's read (data model §3.3, §12 pattern 18), armed from slice 2 rather than 4
    because it is meaningful from the moment the first decision is taken.

    Six content-free columns, named exactly — `organization_id`, `id`, `request_type`, `status`,
    `decided_at`, `application_state` — never `summary`, `request_note` or `comment`. The outbox
    role's slice-1 grant (`apply_schema_grants`, `SELECT, UPDATE` on `outbox` only) does not reach
    this table at all; this is additive and narrower than a blanket `SELECT` would be, so a future
    `SELECT *` in that alarm's query fails loudly with `42501` instead of quietly widening a
    BYPASSRLS surface onto customer content.

    The seven-column grant on `approval_steps` for the SLA sweep's own discovery (data model §3.3)
    is **not** granted here — that reader serves probes armed in slice 4, and granting it early
    buys nothing since nothing yet queries it.

    Called from `do_run_migrations` after **every** `alembic` run, upgrade or downgrade alike —
    there is no direction argument to key off here, so `REVOKE` first and `GRANT` again only if
    the table still exists, rather than `GRANT`-only: a `GRANT`-only version left the privilege in
    place forever once first issued, since a downgrade run reached this function too but had
    nothing in it that would ever take the privilege back. Revoking unconditionally (whenever the
    table exists) and re-granting exactly the same six columns is idempotent on every ordinary
    upgrade — the two statements just repeat — and is what actually removes the grant on a
    downgrade that drops the table (`REVOKE` before `DROP TABLE` is redundant there, since
    Postgres drops dependent privileges with the table, but is what protects a *future* downgrade
    that removes only a column this grant names, rather than the whole table).
    """
    reader = cfg.db_outbox_role
    if not reader:
        return
    if not connection.execute(
        text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": reader}
    ).scalar():
        return
    if connection.execute(
        text("SELECT to_regclass(:name)"), {"name": f"{SCHEMA}.approval_requests"}
    ).scalar():
        connection.execute(text(f'REVOKE ALL ON "{SCHEMA}"."approval_requests" FROM "{reader}"'))
        connection.execute(
            text(
                f"GRANT SELECT (organization_id, id, request_type, status, decided_at, "
                f'application_state) ON "{SCHEMA}"."approval_requests" TO "{reader}"'
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
    grant_unapplied_alarm_reader(connection)


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
