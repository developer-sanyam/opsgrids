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
from app.manufacturing import models as _manufacturing_models  # noqa: F401  (registers tables)
from app.models import SCHEMA, Base
from app.mrp import models as _mrp_models  # noqa: F401  (registers tables)
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


def _is_downgrade() -> bool:
    """An upgrade commits each revision on its own, so a long chain does not hold the early
    revisions' ACCESS EXCLUSIVE locks to the end. A downgrade stays one transaction: its guards
    refuse partway (stock in transit, rows that cannot be dropped) and must leave nothing undone."""
    cmd = getattr(config.cmd_opts, "cmd", None)
    return bool(cmd) and cmd[0].__name__ == "downgrade"


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
        transaction_per_migration=not _is_downgrade(),
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
    "scan_events",
    "shipment_transitions",
    "channel_sync_logs",
    "integration_events",
    "roll_events",
    "lot_genealogy",
    "putaway_captures",
    "goods_receipts",
    "goods_receipt_lines",
    "stock_transfer_transitions",
    "inventory_import_runs",
    "production_outputs",
    "production_wastage",
    "production_genealogy",
    "job_work_receipts",
)
#: What each sweep's cross-tenant discovery reads — `app/cron.py`'s `ReconcileSweep` and
#: `AlertSweep`, `app/channels/sweep.py`'s `ChannelSyncSweep`, `app/mrp/sweep.py`'s
#: `MrpRunSweep`. Column-level, matching exactly
#: what each sweep's own discovery SQL references: `AlertSweep`'s `_ALERT_ORGANIZATIONS_SQL`
#: filters `inventory_items`/`shipments` on `deleted_at` but not `pick_tasks`; `ChannelSyncSweep`'s
#: `_CONNECTED_CHANNELS_SQL` also selects `id` and filters on `status`. F11 and F12 each shipped
#: a new discovery query without adding its tables here — PostgreSQL then refuses column access
#: the outbox role was never granted, and the sweep fails every tick, forever, with no migration
#: ever reporting an error (MAJOR, production incident 2026-10-01: both sweeps had been failing
#: continuously since the deploy that introduced them — `channels.sync.tick_failed`/
#: `operations.alerts.tick_failed`, hundreds of consecutive failures, `InsufficientPrivilegeError`
#: on `channels` and `inventory_items`). Extend this dict, never widen a sweep's own SQL to avoid
#: extending it.
RECONCILE_DISCOVERY_TABLES: dict[str, tuple[str, ...]] = {
    "inventory_balances": ("organization_id",),
    "inventory_ledger": ("organization_id",),
    "inventory_items": ("organization_id", "deleted_at"),
    "pick_tasks": ("organization_id",),
    "shipments": ("organization_id", "deleted_at"),
    # `kind`: the channel sweep polls `sales` connections only. `provider`: the public integration
    # webhook (`app/channels/webhooks.py::webhook_channel`) resolves a connection's tenant before
    # any is bound, and checks the provider it names. Neither is a secret.
    "channels": ("organization_id", "id", "status", "deleted_at", "kind", "provider"),
    # Phase 3: the reservation-expiry sweep asks which organizations hold an active reservation
    # past its `expires_at`.
    "inventory_reservations": ("organization_id", "status", "expires_at"),
    "mrp_runs": ("organization_id", "status"),
    # `app/jobwork/sweep.py`'s `_OVERDUE_ORGANIZATIONS_SQL` — Phase 6; exactly the columns it names.
    "job_work_orders": (
        "organization_id",
        "status",
        "due_date",
        "overdue_notified_at",
        "deleted_at",
    ),
}


def _exists(connection: Connection, table: str) -> bool:
    return bool(
        connection.execute(
            text("SELECT to_regclass(:name)"), {"name": f"{SCHEMA}.{table}"}
        ).scalar()
    )


def grant_reconcile_discovery(connection: Connection) -> None:
    """Column-level `SELECT` for the outbox role, exactly the columns each sweep's own discovery
    query references — never a whole table.

    Every sweep that discovers cross-tenant (`app/cron.py`'s `ReconcileSweep`/`AlertSweep`,
    `app/channels/sweep.py`'s `ChannelSyncSweep`) has to find out *which organizations* (or,
    for channels, which connections) it has work for before it can bind a tenant, and as the
    NOBYPASSRLS app role that question returns nothing — a sweep that checks no one looks
    exactly like one that found nothing to do. The same split approval's SLA sweep uses: the
    BYPASSRLS outbox role discovers, the app role does every read that matters under the tenant
    policy. Column-level, not the table: the bypass buys each sweep's own discovery columns and
    nothing about a tenant's actual data.

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
    for table, columns in RECONCILE_DISCOVERY_TABLES.items():
        if _exists(connection, table):
            connection.execute(text(f'REVOKE ALL ON "{SCHEMA}"."{table}" FROM "{reader}"'))
            # Only the columns this revision has: a downgrade past the migration that added one
            # (`inventory_reservations.expires_at`) must not fail on granting a column that is gone.
            present = {
                row[0]
                for row in connection.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_schema = :s AND table_name = :t"
                    ),
                    {"s": SCHEMA, "t": table},
                )
            }
            wanted = [column for column in columns if column in present]
            if not wanted:
                continue
            column_list = ", ".join(wanted)
            connection.execute(
                text(f'GRANT SELECT ({column_list}) ON "{SCHEMA}"."{table}" TO "{reader}"')
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
