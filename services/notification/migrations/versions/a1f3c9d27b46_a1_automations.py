"""automation workflow engine: automations, immutable versions, runs and run steps

`a1_automations` is the editable shell (name, status, draft graph). `a1_versions` holds each
published graph and is **immutable**: a trigger refuses UPDATE and DELETE, so "plan versions are
immutable" is a property of the database, not of the code that happens to be written today (the
trigger, unlike a table privilege, survives `apply_schema_grants` re-granting everything on the
next migration). `a1_runs` is unique per `(organization_id, automation_id, source_event_id)`,
which is the idempotency guarantee: one run per automation per triggering event. `a1_run_steps` is
the per-node trail.

No table here has a foreign key to another: `automation_id`, `version_id` and `run_id` are plain
indexed UUIDs, the same as `organization_id`, so a later split of this schema stays a config
change. Row-level security is hand-written in the shape of every other tenant table (ENABLE +
FORCE, `WITH CHECK` as well as `USING`).

Revision ID: a1f3c9d27b46
Revises: d5a7c3e91f48
Create Date: 2026-09-26 11:45:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a1f3c9d27b46"  # pragma: allowlist secret
down_revision: str | None = "d5a7c3e91f48"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "notification"
APP_ROLE = "opsgrids_notification"
OUTBOX_ROLE = "opsgrids_outbox"
TABLES = ("a1_automations", "a1_versions", "a1_runs", "a1_run_steps")


def _org_index(table: str) -> None:
    op.create_index(
        op.f(f"ix_notification_{table}_organization_id"),
        table,
        ["organization_id"],
        unique=False,
        schema=SCHEMA,
    )


def upgrade() -> None:
    op.create_table(
        "a1_automations",
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("trigger_event", sa.String(length=120), nullable=False),
        sa.Column("module", sa.String(length=40), nullable=False),
        sa.Column("draft_graph", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("current_version_id", sa.UUID(), nullable=True),
        sa.Column("current_version", sa.Integer(), nullable=True),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column("updated_by", sa.String(length=64), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('draft', 'active', 'paused')", name=op.f("ck_a1_automations_status")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_a1_automations")),
        schema=SCHEMA,
    )
    _org_index("a1_automations")
    op.create_index(
        "ix_a1_automations_org_status",
        "a1_automations",
        ["organization_id", "status"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_a1_automations_org_trigger",
        "a1_automations",
        ["organization_id", "trigger_event", "status"],
        schema=SCHEMA,
    )

    op.create_table(
        "a1_versions",
        sa.Column("automation_id", sa.UUID(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("trigger_event", sa.String(length=120), nullable=False),
        sa.Column("graph", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("published_by", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_a1_versions")),
        sa.UniqueConstraint(
            "organization_id", "automation_id", "version", name="uq_a1_versions_org_auto_version"
        ),
        schema=SCHEMA,
    )
    _org_index("a1_versions")

    op.create_table(
        "a1_runs",
        sa.Column("automation_id", sa.UUID(), nullable=False),
        sa.Column("version_id", sa.UUID(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("source_event_id", sa.UUID(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=120), nullable=False),
        sa.Column("state", sa.String(length=20), nullable=False),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("subject", sa.String(length=254), nullable=False),
        sa.Column("failure_code", sa.String(length=80), nullable=True),
        sa.Column("current_node_id", sa.String(length=64), nullable=True),
        sa.Column("next_wake_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("context_enc", sa.LargeBinary(), nullable=False),
        sa.Column("wake_token", sa.String(length=64), nullable=True),
        sa.Column("step_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.CheckConstraint(
            "state IN ('running', 'waiting', 'completed', 'failed', 'skipped')",
            name=op.f("ck_a1_runs_state"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_a1_runs")),
        sa.UniqueConstraint(
            "organization_id", "automation_id", "source_event_id", name="uq_a1_runs_org_auto_event"
        ),
        schema=SCHEMA,
    )
    _org_index("a1_runs")
    op.create_index(
        "ix_a1_runs_org_auto_started",
        "a1_runs",
        ["organization_id", "automation_id", "started_at"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_a1_runs_org_state_wake",
        "a1_runs",
        ["organization_id", "state", "next_wake_at"],
        schema=SCHEMA,
    )

    op.create_table(
        "a1_run_steps",
        sa.Column("run_id", sa.UUID(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("node_id", sa.String(length=64), nullable=False),
        sa.Column("type", sa.String(length=20), nullable=False),
        sa.Column("state", sa.String(length=20), nullable=False),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("detail", sa.String(length=500), nullable=True),
        sa.Column("message_id", sa.UUID(), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.CheckConstraint(
            "state IN ('waiting', 'running', 'done', 'failed', 'skipped')",
            name=op.f("ck_a1_run_steps_state"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_a1_run_steps")),
        schema=SCHEMA,
    )
    _org_index("a1_run_steps")
    op.create_index(
        "ix_a1_run_steps_org_run",
        "a1_run_steps",
        ["organization_id", "run_id", "seq"],
        schema=SCHEMA,
    )

    for table in TABLES:
        op.execute(f"ALTER TABLE {SCHEMA}.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {SCHEMA}.{table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"""
            CREATE POLICY tenant_isolation ON {SCHEMA}.{table}
                FOR ALL TO {APP_ROLE}
                USING      (organization_id = {SCHEMA}.current_org())
                WITH CHECK (organization_id = {SCHEMA}.current_org())
            """
        )

    # Immutable versions. A trigger rather than a revoked privilege: `apply_schema_grants`
    # re-grants UPDATE/DELETE on every table after each migration run and would undo a revoke.
    op.execute(
        f"""
        CREATE FUNCTION {SCHEMA}.a1_versions_immutable() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'a1_versions rows are immutable: publish a new version instead';
        END
        $$
        """
    )
    op.execute(
        f"""
        CREATE TRIGGER a1_versions_immutable
            BEFORE UPDATE OR DELETE ON {SCHEMA}.a1_versions
            FOR EACH ROW EXECUTE FUNCTION {SCHEMA}.a1_versions_immutable()
        """
    )

    # The overdue-wait sweep's cross-tenant discovery (`app/cron.py`) runs as the outbox role.
    # Three columns, named exactly, so a bug in that query cannot read a run's context. The
    # grant survives `apply_schema_grants`, which only ever gives that role `outbox`.
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{OUTBOX_ROLE}') THEN
                GRANT USAGE ON SCHEMA {SCHEMA} TO "{OUTBOX_ROLE}";
                GRANT SELECT (organization_id, state, next_wake_at)
                    ON {SCHEMA}.a1_runs TO "{OUTBOX_ROLE}";
            END IF;
        END
        $$
        """  # noqa: S608 - names are module constants, not input
    )


def downgrade() -> None:
    op.execute(f"DROP TRIGGER IF EXISTS a1_versions_immutable ON {SCHEMA}.a1_versions")
    op.execute(f"DROP FUNCTION IF EXISTS {SCHEMA}.a1_versions_immutable()")
    for table in reversed(TABLES):
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {SCHEMA}.{table}")
        op.drop_table(table, schema=SCHEMA)
