"""ims tables under RLS, and decisions append-only

The two guarantees for the four tables the previous migration added.

**Row-level security**, on all four. Every one carries `organization_id` and holds tenant data, so
each gets the same `tenant_isolation` policy every other finance table has — `USING` *and*
`WITH CHECK`, `ENABLE` *and* `FORCE`, for the reasons the original RLS migration sets out. None of
them is vocabulary: a supplier's claim against a company, that company's purchase book, and what
it decided about either are as tenant-specific as data gets. Leaking a competitor's supplier list
would be worse than leaking their invoices.

**Append-only on `ims_decisions`**, by trigger. The tidy expression would be
`REVOKE UPDATE, DELETE ON finance.ims_decisions FROM opsgrids_finance`, and it does not hold:
`migrations/env.py` runs `apply_schema_grants` at the end of every migration run, which `GRANT`s
UPDATE and DELETE on *all* tables in the schema and only re-revokes `audit_logs`. A revoke written
here is undone moments later, in the same `alembic upgrade`. The platform has now walked into that
twice — payment's `refunds`/`provider_events` and this service's own `einvoice_filings` — so the
trigger is not a stylistic preference, it is the only mechanism that survives the re-grant.

`finance.forbid_append_only_mutation()` already exists; the previous append-only migration created
it. It is created here with `CREATE OR REPLACE` anyway, so this migration stands on a database
where that one has been rolled back and so that neither downgrade leaves the other's trigger
pointing at a function that is gone.

**Why `ims_decisions` and not `ims_reconciliations`.** They look alike and they are opposite. A
decision is a fact about a person: somebody accepted a supplier's claim on this business, at a
time, and the input tax credit that follows is theirs to answer for. Evidence the application can
rewrite is not evidence. A reconciliation is a *suggestion* — recomputed from the purchase book
every time it changes, deliberately re-runnable, and worth nothing to an auditor frozen. Making it
immutable would only mean a stale opinion could never be superseded, which is the opposite of the
point.

`purchase_documents` and `ims_documents` are likewise mutable at the database. Neither has an
update path in the service, and both are effectively write-once, but the guarantee that matters is
the decision ledger's — `ims_documents.status` is a projection maintained by
`services.record_ims_decision` in the same transaction as the decision that moves it, which is an
UPDATE by construction and could not coexist with a trigger refusing one.

Revision ID: b62d9f4c17ae
Revises: ebbf416aa968
Create Date: 2026-08-25 18:04:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "b62d9f4c17ae"  # pragma: allowlist secret
down_revision: str | None = "ebbf416aa968"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "finance"
APP_ROLE = "opsgrids_finance"

#: All four are tenant-scoped. None is vocabulary, so none joins `tax_rates` and
#: `processed_events` on the unpolicied list.
TENANT_TABLES = (
    "purchase_documents",
    "ims_documents",
    "ims_reconciliations",
    "ims_decisions",
)

#: Written once, never touched again. See the module docstring for why the suggestion table is
#: deliberately absent from this list.
APPEND_ONLY_TABLES = ("ims_decisions",)


def upgrade() -> None:
    for table in TENANT_TABLES:
        op.execute(f"ALTER TABLE {SCHEMA}.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {SCHEMA}.{table} FORCE ROW LEVEL SECURITY")
        # `current_org()` already exists — the first RLS migration created it, and it reads the
        # transaction-local GUC with `nullif(..., '')` so an expired binding is a false predicate
        # rather than a cast error.
        #
        # WITH CHECK is not optional. USING is evaluated against existing rows and is not applied
        # to the new row of an INSERT, so without it a tenant could insert a supplier document
        # belonging to somebody else — invisible to them, fully visible to the victim.
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
    # The function is deliberately left in place: `einvoice_filings` still uses it, and dropping
    # it here would disarm that table's trigger as a side effect of rolling back this slice.

    for table in TENANT_TABLES:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {SCHEMA}.{table}")
        op.execute(f"ALTER TABLE {SCHEMA}.{table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {SCHEMA}.{table} DISABLE ROW LEVEL SECURITY")
