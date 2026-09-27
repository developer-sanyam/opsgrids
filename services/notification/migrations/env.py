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


def grant_campaign_sweep_reader(connection: Connection) -> None:
    """Column-level `SELECT` on `campaigns` for the outbox role — the campaign send driver's own
    cross-tenant discovery (`app/cron.py`, `selectors.due_campaigns`).

    The outbox role's slice-1 grant (`apply_schema_grants`, `SELECT, UPDATE` on `outbox` only)
    does not reach this table at all, and discovery must not run BYPASSRLS with a blanket
    `SELECT *` — that would let a bug in one cross-tenant query read a subject line or a body
    nobody but that campaign's own organization may see. Six columns, named exactly:
    `organization_id`, `id`, `status`, `paused_at`, `started_at`, `deleted_at` — never `subject`,
    `html_body`, `text_body`, or `sender_id`.

    Same idempotent shape as `services/approval/migrations/env.py`'s `grant_sla_sweep_reader`,
    called after **every** alembic run for the same reason: `REVOKE` first and `GRANT` again only
    if the table still exists, rather than `GRANT`-only, which would leave the privilege in place
    forever once issued and never take it back on a downgrade that drops the table.
    """
    reader = cfg.db_outbox_role
    if not reader:
        return
    if not connection.execute(
        text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": reader}
    ).scalar():
        return
    if connection.execute(
        text("SELECT to_regclass(:name)"), {"name": f"{SCHEMA}.campaigns"}
    ).scalar():
        connection.execute(text(f'REVOKE ALL ON "{SCHEMA}"."campaigns" FROM "{reader}"'))
        connection.execute(
            text(
                "GRANT SELECT (organization_id, id, status, paused_at, started_at, deleted_at) "
                f'ON "{SCHEMA}"."campaigns" TO "{reader}"'
            )
        )
    connection.commit()


def grant_message_retry_reader(connection: Connection) -> None:
    """Column-level `SELECT` on `messages` for the outbox role — the failed-send retry sweep's own
    cross-tenant discovery (`app/cron.py`, `services.due_message_retries`).

    Same shape and reasons as `grant_campaign_sweep_reader`: seven columns, named exactly
    (`organization_id`, `id`, `state`, `attempts`, `created_at`, `updated_at`, `deleted_at`;
    `created_at` is the retry window's clock), never `to_address`, `subject` or `body`. Discovery
    only asks *which message in which organization is due for another attempt*; every write the
    sweep makes runs tenant-bound. Without this grant the sweep's query fails on the
    outbox role and no failed send is ever retried.

    Idempotent and re-applied after every alembic run: `REVOKE` first, `GRANT` again only if the
    table exists.
    """
    reader = cfg.db_outbox_role
    if not reader:
        return
    if not connection.execute(
        text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": reader}
    ).scalar():
        return
    if connection.execute(
        text("SELECT to_regclass(:name)"), {"name": f"{SCHEMA}.messages"}
    ).scalar():
        connection.execute(text(f'REVOKE ALL ON "{SCHEMA}"."messages" FROM "{reader}"'))
        connection.execute(
            text(
                "GRANT SELECT (organization_id, id, state, attempts, created_at, updated_at, "
                "deleted_at) "
                f'ON "{SCHEMA}"."messages" TO "{reader}"'
            )
        )
    connection.commit()


#: Append-only at the database, enforced by a table privilege rather than a policy (ADR-0000 §9):
#: a consent change of mind is a new row, never an edit (ADR-0021).
APPEND_ONLY_TABLE = "consent_events"


def reapply_append_only(connection: Connection) -> None:
    """Re-revoke UPDATE/DELETE on `consent_events` from the app role, after the blanket re-grant.

    `apply_schema_grants` re-grants UPDATE/DELETE on every table each run and only re-revokes
    `audit_logs`, so the revoke in the migration alone would be undone by the next `alembic
    upgrade`. Re-applying it here is what makes append-only hold across grant re-application (the
    shape the console uses for `revenue_events`). Guarded on the table existing so a downgrade
    past it is a no-op.
    """
    if not cfg.db_app_role:
        return
    for table in (APPEND_ONLY_TABLE, "email_events", "w1_meta_deletion_requests"):
        if connection.execute(
            text("SELECT to_regclass(:name)"), {"name": f"{SCHEMA}.{table}"}
        ).scalar():
            connection.execute(
                text(f'REVOKE UPDATE, DELETE ON {SCHEMA}.{table} FROM "{cfg.db_app_role}"')
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
    grant_campaign_sweep_reader(connection)
    grant_message_retry_reader(connection)
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
