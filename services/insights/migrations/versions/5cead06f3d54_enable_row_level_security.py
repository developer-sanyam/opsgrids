"""enable row level security

The same shape as approval-, attendance- and account-service's own RLS migrations (ADR-0002 §5,
`docs/insights/data-model.md` §1): every table that carries `organization_id` gets
`ENABLE`/`FORCE ROW LEVEL SECURITY` and a `tenant_isolation` policy comparing it against the
transaction-local GUC through this schema's own `current_org()` function — never a shared
`public` helper, so a later database-per-service split stays a config change.

`processed_events` is deliberately **not** policied: it carries no `organization_id` (the
platform mixin every consumer uses, ADR-0020 risk 8) — a policy here would make every
`already_processed` lookup return nothing and a redelivered event would be reprocessed in
silence (ADR-0002 §5).

This migration also grants `opsgrids_outbox` — BYPASSRLS, otherwise confined to `outbox` itself
by the scaffold's own `apply_schema_grants` — the two narrow, **column-level** SELECT grants
`docs/insights/data-model.md` §1 names: `org_profile(organization_id, timezone)` and
`backfill_runs(id, organization_id, entity, status, next_attempt_at, finished_at)`. This is what
lets the snapshot sweep and the backfill runner *discover* which organizations have work — there
is no tenant to bind while asking that question (ADR-0002 §2) — without ever being able to read
a fact. "It cannot read a fact" is enforced here, not by convention: no other column, and no
other table, is granted to this role in this migration or any other.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
from sqlalchemy import text as sa_text

revision: str = "5cead06f3d54"
down_revision: str | None = "db131f3cf5a1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "insights"
APP_ROLE = "opsgrids_insights"
OUTBOX_ROLE = "opsgrids_outbox"

#: Tenant-scoped: every one of these gets a policy.
TENANT_TABLES = (
    "audit_logs",
    "outbox",
    "org_profile",
    "daily_snapshots",
    "metric_coverage",
    "backfill_runs",
)

#: Deliberately **not** policied — see the module docstring.
UNPOLICIED = ("processed_events",)


def _enable(table: str) -> None:
    op.execute(f'ALTER TABLE {SCHEMA}."{table}" ENABLE ROW LEVEL SECURITY')
    op.execute(f'ALTER TABLE {SCHEMA}."{table}" FORCE ROW LEVEL SECURITY')


def upgrade() -> None:
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION {SCHEMA}.current_org() RETURNS uuid
            LANGUAGE sql STABLE PARALLEL SAFE
        AS $$ SELECT nullif(current_setting('app.organization_id', true), '')::uuid $$
        """
    )
    for table in TENANT_TABLES:
        _enable(table)
        # WITH CHECK is not optional. USING is evaluated against existing rows and is not
        # applied to the new row of an INSERT, so without it a tenant could insert a row
        # belonging to somebody else — invisible to them, fully visible to the victim
        # (data-model.md §1).
        op.execute(
            f"""
            CREATE POLICY tenant_isolation ON {SCHEMA}."{table}"
                FOR ALL TO {APP_ROLE}
                USING      (organization_id = {SCHEMA}.current_org())
                WITH CHECK (organization_id = {SCHEMA}.current_org())
            """
        )

    # The narrow bypass, guarded the same way `platform_core.db.apply_schema_grants` guards its
    # own outbox grant: a role that does not exist yet (a database that has never run
    # `reconcile-roles`) must not fail the migration.
    bind = op.get_bind()
    role_exists = bind.execute(
        sa_text("SELECT 1 FROM pg_roles WHERE rolname = :role"), {"role": OUTBOX_ROLE}
    ).scalar()
    if role_exists:
        op.execute(f'GRANT USAGE ON SCHEMA "{SCHEMA}" TO {OUTBOX_ROLE}')
        op.execute(
            f'GRANT SELECT (organization_id, timezone) ON {SCHEMA}.org_profile TO {OUTBOX_ROLE}'
        )
        op.execute(
            f"GRANT SELECT (id, organization_id, entity, status, next_attempt_at, finished_at) "
            f"ON {SCHEMA}.backfill_runs TO {OUTBOX_ROLE}"
        )


def downgrade() -> None:
    bind = op.get_bind()
    role_exists = bind.execute(
        sa_text("SELECT 1 FROM pg_roles WHERE rolname = :role"), {"role": OUTBOX_ROLE}
    ).scalar()
    if role_exists:
        op.execute(f"REVOKE SELECT ON {SCHEMA}.backfill_runs FROM {OUTBOX_ROLE}")
        op.execute(f"REVOKE SELECT ON {SCHEMA}.org_profile FROM {OUTBOX_ROLE}")

    for table in TENANT_TABLES:
        op.execute(f'DROP POLICY IF EXISTS tenant_isolation ON {SCHEMA}."{table}"')
        op.execute(f'ALTER TABLE {SCHEMA}."{table}" NO FORCE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE {SCHEMA}."{table}" DISABLE ROW LEVEL SECURITY')
    op.execute(f"DROP FUNCTION IF EXISTS {SCHEMA}.current_org()")
