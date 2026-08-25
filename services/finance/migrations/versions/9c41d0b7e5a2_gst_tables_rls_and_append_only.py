"""gst tables under RLS, and filings append-only

Two guarantees for the two tables the previous migration added.

**Row-level security**, on both. `gst_registrations` and `einvoice_filings` carry
`organization_id` and hold tenant data, so they get the same `tenant_isolation` policy every other
finance table has — `USING` *and* `WITH CHECK`, `ENABLE` *and* `FORCE`, for the reasons the
original RLS migration sets out. Neither is vocabulary: a GSTIN belongs to one company, and a
filing history is the evidence of one company's reporting.

**Append-only on `einvoice_filings`**, by trigger. The tidy expression would be
`REVOKE UPDATE, DELETE ON finance.einvoice_filings FROM atmora_finance`, and it does not hold:
`migrations/env.py` runs `apply_schema_grants` at the end of every migration run, which `GRANT`s
UPDATE and DELETE on *all* tables in the schema and only re-revokes `audit_logs`. A revoke written
here is undone moments later, in the same `alembic upgrade` — which is the trap the platform
already walked into once, and why payment's `refunds`/`provider_events` and subscription's
transition table use triggers instead. A `BEFORE UPDATE OR DELETE` trigger is independent of
privileges: it fires whatever the grant state, so the guarantee survives the post-migration
re-grant.

The trigger also has to be right about *why* the table is append-only. A filing is evidence: it is
what the platform can show an auditor to prove that a document was reported, when, and what the
portal answered. Evidence the application can rewrite is not evidence. A retry is a new row, a
cancellation is a new row, and a mistake stays visible with the correction after it — which is the
same shape invoices already have, and the reason GST's own model (corrections are credit notes,
never edits) fits this platform without adaptation.

`gst_registrations` is deliberately **not** append-only: it is configuration, not evidence. A
trade name changes. What must never change is the GSTIN a filed invoice was filed under, and that
is safe because `invoices.seller_gstin` snapshots it rather than joining to this table.

Revision ID: 9c41d0b7e5a2
Revises: 3713508bf67c
Create Date: 2026-08-25 16:52:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "9c41d0b7e5a2"  # pragma: allowlist secret
down_revision: str | None = "3713508bf67c"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "finance"
APP_ROLE = "atmora_finance"

#: Both new tables are tenant-scoped. Neither is vocabulary, so neither joins `tax_rates` and
#: `processed_events` on the unpolicied list.
TENANT_TABLES = ("gst_registrations", "einvoice_filings")

#: Written once, never touched again.
APPEND_ONLY_TABLES = ("einvoice_filings",)


def upgrade() -> None:
    for table in TENANT_TABLES:
        op.execute(f"ALTER TABLE {SCHEMA}.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {SCHEMA}.{table} FORCE ROW LEVEL SECURITY")
        # `current_org()` already exists — the first RLS migration created it, and it reads the
        # transaction-local GUC with `nullif(..., '')` so an expired binding is a false predicate
        # rather than a cast error.
        op.execute(
            f"""
            CREATE POLICY tenant_isolation ON {SCHEMA}.{table}
                FOR ALL TO {APP_ROLE}
                USING      (organization_id = {SCHEMA}.current_org())
                WITH CHECK (organization_id = {SCHEMA}.current_org())
            """
        )

    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION {SCHEMA}.forbid_append_only_mutation()
            RETURNS trigger
            LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION
                '%.% is append-only; a correction is a new row', TG_TABLE_SCHEMA, TG_TABLE_NAME
                USING ERRCODE = 'restrict_violation';
        END;
        $$
        """
    )
    for table in APPEND_ONLY_TABLES:
        op.execute(
            f"""
            CREATE TRIGGER trg_{table}_append_only
                BEFORE UPDATE OR DELETE ON {SCHEMA}.{table}
                FOR EACH ROW
                EXECUTE FUNCTION {SCHEMA}.forbid_append_only_mutation()
            """
        )


def downgrade() -> None:
    for table in APPEND_ONLY_TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_append_only ON {SCHEMA}.{table}")
    op.execute(f"DROP FUNCTION IF EXISTS {SCHEMA}.forbid_append_only_mutation()")

    for table in TENANT_TABLES:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {SCHEMA}.{table}")
        op.execute(f"ALTER TABLE {SCHEMA}.{table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {SCHEMA}.{table} DISABLE ROW LEVEL SECURITY")
