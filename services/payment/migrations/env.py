"""Alembic environment — async, schema per service.

The schema is created before the first migration runs, so a fresh database needs no
manual DDL. `version_table_schema` keeps this service's alembic_version inside its own
schema rather than a shared public one.
"""

from __future__ import annotations

import asyncio
import warnings
from logging.config import fileConfig

from alembic import context
from sqlalchemy import exc as sa_exc
from sqlalchemy import pool, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from app.config import settings
from app.models import SCHEMA, Base
from app.selectors import SWEEP_READER_CHARGE_COLUMNS
from platform_core.db import apply_schema_grants

# `provider_events` carries a CHECK created `NOT VALID` on purpose (migration f6a7b8c9d1e2: the
# table is append-only, so a legacy row keyed '' could never be corrected, and the constraint must
# still apply). Postgres reflects such a constraint with dialect options that SQLAlchemy 2.0 then
# warns it cannot validate, on every `alembic check` / autogenerate pass. Cosmetic; silenced by
# message so no other SAWarning is.
warnings.filterwarnings(
    "ignore",
    message=r"Can't validate argument '(dialect_options|not_valid)'",
    category=sa_exc.SAWarning,
)

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

cfg = settings()
# Migrations run as the admin role: it owns the schema, and DDL is not subject to
# row-level security. The API connects as a different, NOBYPASSRLS role.
config.set_main_option("sqlalchemy.url", cfg.admin_dsn)
target_metadata = Base.metadata


def grant_sweep_reader(connection: Connection) -> None:
    """Column-level cross-tenant `SELECT` on `charges` for the stuck-charge sweep's discovery
    (`app/cron.py`, D3) — `SWEEP_READER_CHARGE_COLUMNS` and nothing else.

    The sweep has the outbox relay's problem exactly: its query exists to discover *which*
    organizations have a charge staged but never finalized, so there is no tenant to bind while
    asking, and as the NOBYPASSRLS app role it would select nothing and a charge orphaned by a
    crash between the provider call and recording it would stay `created` forever. It reads on
    the relay's role — the platform's "cross-tenant discovery, nothing else" identity.

    **Column-scoped**, mirroring `services/finance/migrations/env.py` and approval's: a future
    `SELECT *` in discovery fails loudly with `42501` instead of widening a BYPASSRLS surface onto
    amounts and keys. `REVOKE ALL` first, then the grant, on every alembic run: the round-1 grant
    here was table-wide, and a `GRANT`-only version would leave it in place forever. Every write
    the sweep makes runs back on the app role inside a tenant-bound transaction.
    """
    reader = cfg.db_outbox_role
    if not reader:
        return
    if not connection.execute(
        text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": reader}
    ).scalar():
        return
    if connection.execute(
        text("SELECT to_regclass(:name)"), {"name": f"{SCHEMA}.charges"}
    ).scalar():
        connection.execute(text(f'REVOKE ALL ON "{SCHEMA}"."charges" FROM "{reader}"'))
        # Only the columns that exist at this revision: this runs after a downgrade too, and a
        # downgrade past the lease migration removes four of them.
        present = set(
            connection.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = :s AND table_name = 'charges'"
                ),
                {"s": SCHEMA},
            ).scalars()
        )
        columns = ", ".join(c for c in SWEEP_READER_CHARGE_COLUMNS if c in present)
        connection.execute(text(f'GRANT SELECT ({columns}) ON "{SCHEMA}"."charges" TO "{reader}"'))
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
