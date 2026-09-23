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


#: What the sweeps may read across tenants, and nothing else (ADR-0005 §6 and its 2026-09-23
#: addendum). `dunning_cycles` whole, for the dunning ladder. The billing run's two tables by
#: **column**: exactly what its discovery and the `/health/billing-run` probe read — ids, states
#: and timestamps, never an amount, a pin, an item or an invoice id's neighbours. A `None` means the
#: whole table.
SWEEP_READABLE: dict[str, tuple[str, ...] | None] = {
    "dunning_cycles": None,
    "subscriptions": ("id", "organization_id", "state", "trial_ends_at", "current_period_end"),
    "billing_periods": (
        "id",
        "organization_id",
        "subscription_id",
        "kind",
        "state",
        "lease_until",
        "next_attempt_at",
        "attempts",
        "last_error",
        "permanent_failure",
        "invoice_id",
        "created_at",
        "recorded_at",
    ),
}


def grant_sweep_reader(connection: Connection) -> None:
    """Cross-tenant SELECT for the sweeps' discovery reads (`SWEEP_READABLE`).

    The sweeps (`app/cron.py`, `app/billing_run.py`) have the outbox relay's problem exactly: their
    queries exist to discover *which* organizations have work due, so there is no tenant to bind
    while asking, and as the NOBYPASSRLS app role they would select nothing, log nothing, and leave
    every past-due subscription in grace forever. They therefore read on the relay's role — the
    platform's already provisioned "cross-tenant discovery, nothing else" identity.

    **SELECT only, and only these columns.** Every write either sweep makes runs back on the app
    role inside a tenant-bound transaction; the bypass buys discovery and never authority. A
    table-level grant an earlier revision of this function made is revoked first, so an existing
    database narrows to the column list rather than keeping both.

    Runs here rather than in the bootstrap SQL because the tables do not exist when that runs, and
    is guarded by `to_regclass` so a downgrade that has dropped one is a no-op rather than an error.
    """
    reader = cfg.db_outbox_role
    if not reader:
        return
    if not connection.execute(
        text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": reader}
    ).scalar():
        return
    for table, columns in SWEEP_READABLE.items():
        if not connection.execute(
            text("SELECT to_regclass(:name)"), {"name": f"{SCHEMA}.{table}"}
        ).scalar():
            continue
        if columns is None:
            connection.execute(text(f'GRANT SELECT ON "{SCHEMA}"."{table}" TO "{reader}"'))
            continue
        # Identifiers from the constant table above and the configured role — never input.
        connection.execute(text(f'REVOKE SELECT ON "{SCHEMA}"."{table}" FROM "{reader}"'))  # noqa: S608
        listed = ", ".join(f'"{c}"' for c in columns)
        connection.execute(text(f'GRANT SELECT ({listed}) ON "{SCHEMA}"."{table}" TO "{reader}"'))
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
    grant_sweep_reader(connection)


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
