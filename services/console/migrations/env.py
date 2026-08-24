"""Alembic environment — async, schema per service.

The schema is created before the first migration runs, so a fresh database needs no
manual DDL. `version_table_schema` keeps this service's alembic_version inside its own
schema rather than a shared public one.

Two console-specific steps run after `apply_schema_grants`, for the same reason every grant in
this file runs here rather than in the bootstrap SQL: the tables do not exist when the bootstrap
runs, and a step somebody must remember is a step somebody eventually forgets.

* `grant_directory_reader` gives `atmora_console_directory` cross-tenant SELECT on the projection
  and audit tables — this is what makes the operator's read API work, and it is SELECT-only so a
  bug in that read path can never write (ADR-0002 §2, copied from product-service).
* the append-only re-revoke keeps `revenue_events` append-only across grant re-application:
  `apply_schema_grants` re-grants UPDATE/DELETE on every table and only re-revokes `audit_logs`, so
  without this the revenue log's revoke would be undone on the next `alembic upgrade` (the same
  interaction metering handles for `usage_events`).
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

#: The projection and audit tables the operator reads cross-tenant. SELECT only — no write ever
#: reaches the directory role, so a bug in the read path cannot write across tenants.
DIRECTORY_READER = "atmora_console_directory"
DIRECTORY_TABLES = ("org_commercial_state", "revenue_events", "usage_summary", "audit_logs")

#: Append-only at the database, enforced by a table privilege rather than a policy (ADR-0000 §9).
APPEND_ONLY_TABLE = "revenue_events"


def grant_directory_reader(connection: Connection) -> None:
    """Cross-tenant SELECT for the operator read API, on the projection and audit tables.

    `apply_schema_grants` grants the account directory reader (`atmora_directory`); console's
    reader is its own role on its own tables, so it is granted here explicitly — SELECT only. Each
    grant is guarded by `to_regclass`, so a downgrade that has dropped the tables is a no-op.
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


def reapply_append_only(connection: Connection) -> None:
    """Re-revoke UPDATE/DELETE on `revenue_events` from the app role, after the blanket re-grant.

    `apply_schema_grants` re-grants UPDATE/DELETE on every table each run and only re-revokes
    `audit_logs`, so the append-only revoke in the migration would be undone on the next upgrade if
    it lived solely there. Re-applying it here is what makes append-only hold across grant
    re-application (the shape metering uses for `usage_events`). Guarded on the table existing so a
    downgrade-to-base is a no-op.
    """
    if not cfg.db_app_role:
        return
    if connection.execute(
        text("SELECT to_regclass(:name)"), {"name": f"{SCHEMA}.{APPEND_ONLY_TABLE}"}
    ).scalar():
        connection.execute(
            text(f'REVOKE UPDATE, DELETE ON {SCHEMA}.{APPEND_ONLY_TABLE} FROM "{cfg.db_app_role}"')
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
    # without them.
    apply_schema_grants(connection, SCHEMA, cfg.db_app_role)
    grant_directory_reader(connection)
    reapply_append_only(connection)


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
