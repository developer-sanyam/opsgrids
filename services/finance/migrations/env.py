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


#: Exactly the columns the filing-reclaim sweep's four discovery queries read (`selectors.
#: due_filing_reclaims`, `due_stale_filings`, `due_stale_eway`, `due_ims_pushes`) — and not one
#: more. SELECT only; see `grant_sweep_reader`.
_SWEEP_READER_COLUMNS: dict[str, tuple[str, ...]] = {
    # due_filing_reclaims: which reportable, finalized invoices exist, and since when.
    "invoices": (
        "organization_id",
        "id",
        "supply_type",
        "document_type",
        "status",
        "created_at",
        "deleted_at",
    ),
    # due_filing_reclaims' "no filing row at all" probe, and due_stale_filings' newest-row test.
    "einvoice_filings": (
        "organization_id",
        "id",
        "invoice_id",
        "status",
        "attempted_at",
        "created_at",
        "deleted_at",
    ),
    # due_filing_reclaims / due_stale_filings: skip an organization with no registration to file
    # under (the consumer writes nothing for it, so re-queueing would loop).
    "gst_registrations": (
        "organization_id",
        "id",
        "is_primary",
        "deleted_at",
    ),
    # due_stale_eway's newest-row test.
    "eway_bills": (
        "organization_id",
        "id",
        "invoice_id",
        "status",
        "attempted_at",
        "created_at",
        "deleted_at",
    ),
    # due_ims_pushes (C2): which (organization, period) has a push waiting to be sent or a claim
    # that outlived its lease. Identifiers, a status and three timestamps — never the decision's
    # action, the portal reference, the reason a push failed, or what the portal said back.
    "ims_decision_pushes": (
        "organization_id",
        "id",
        "period",
        "status",
        "requested_at",
        "next_attempt_at",
        "lease_until",
        "deleted_at",
    ),
}


def grant_sweep_reader(connection: Connection) -> None:
    """Column-level cross-tenant `SELECT` on `invoices`, `einvoice_filings`, `gst_registrations`,
    `eway_bills` and `ims_decision_pushes`, for the filing-reclaim sweep's discovery queries
    (`app/cron.py`, D3, and C2's IMS push resume).

    The sweep has the outbox relay's problem exactly: its queries exist to discover *which*
    organizations have a reportable invoice the automatic filing pipeline has lost track of, or a
    filing/e-Way claim stuck past its deadline, so there is no tenant to bind while asking, and
    as the NOBYPASSRLS app role they would select nothing, log nothing, and such a document would
    stay unfiled (or half-withdrawn) past its statutory window, silently. They therefore read on
    the relay's role — the platform's already provisioned "cross-tenant discovery, nothing else"
    identity — rather than a new BYPASSRLS role added to the bootstrap for three tables.

    **Column-scoped, not table-wide** (`_SWEEP_READER_COLUMNS`), the same discipline
    `services/approval/migrations/env.py` applies: identifiers, statuses and timestamps, never an
    amount, a GSTIN, an IRN, a vehicle number or a portal error message. A future `SELECT *` in a
    discovery query fails loudly with `42501` instead of quietly widening a BYPASSRLS surface onto
    customer content. `REVOKE ALL` first and `GRANT` again only while the table exists — the
    round-1 grant of this function was table-wide, and a `GRANT`-only version would leave that
    wider privilege in place forever; this is also what takes the grant back on a downgrade.

    **SELECT only.** Every write the sweep makes (re-publishing `filing_requested`, a lease row,
    the portal's answer) runs back on the app role inside a tenant-bound transaction; the bypass
    buys discovery and never authority.
    """
    reader = cfg.db_outbox_role
    if not reader:
        return
    if not connection.execute(
        text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": reader}
    ).scalar():
        return
    for table, columns in _SWEEP_READER_COLUMNS.items():
        if not connection.execute(
            text("SELECT to_regclass(:name)"), {"name": f"{SCHEMA}.{table}"}
        ).scalar():
            continue
        connection.execute(text(f'REVOKE ALL ON "{SCHEMA}"."{table}" FROM "{reader}"'))
        # Only the columns that exist at this revision — this runs after a downgrade too.
        present = set(
            connection.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = :s AND table_name = :t"
                ),
                {"s": SCHEMA, "t": table},
            ).scalars()
        )
        granted = ", ".join(c for c in columns if c in present)
        if granted:
            connection.execute(
                text(f'GRANT SELECT ({granted}) ON "{SCHEMA}"."{table}" TO "{reader}"')
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
