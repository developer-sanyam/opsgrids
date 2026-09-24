"""itc_ledger_entries under RLS, and append-only

The guarantees for the table the previous migration added — the same two, and for the same
reasons `f0b7e51a2c94` gives for the return and withholding tables.

**Row-level security**, `USING` *and* `WITH CHECK`, `ENABLE` *and* `FORCE` — `WITH CHECK` is not
optional, because `USING` alone is not applied to the new row of an INSERT, and without it a
tenant could post a ledger entry belonging to somebody else.

**Append-only, by trigger, not `REVOKE`.** `migrations/env.py` runs `apply_schema_grants` at the
end of every migration run, which re-GRANTs UPDATE and DELETE on every table in the schema; a
revoke written here would be undone in the same `alembic upgrade`. `finance.forbid_append_only_
mutation()` already exists — three earlier migrations created it — so this one only attaches it.
Every row here is evidence of money moving through the credit ledger, and evidence the application
can rewrite is not evidence.

Revision ID: e7d648663241
Revises: 488f37e6c827
Create Date: 2026-09-23 09:10:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "e7d648663241"  # pragma: allowlist secret
down_revision: str | None = "488f37e6c827"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "finance"
APP_ROLE = "opsgrids_finance"
TABLE = "itc_ledger_entries"


def upgrade() -> None:
    op.execute(f"ALTER TABLE {SCHEMA}.{TABLE} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {SCHEMA}.{TABLE} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY tenant_isolation ON {SCHEMA}.{TABLE}
            FOR ALL TO {APP_ROLE}
            USING      (organization_id = {SCHEMA}.current_org())
            WITH CHECK (organization_id = {SCHEMA}.current_org())
        """
    )
    # `finance.forbid_append_only_mutation()` already exists; created again with CREATE OR REPLACE
    # so this migration stands on its own on a database where an earlier one creating it has been
    # rolled back, and so no downgrade of this migration disarms another table's trigger.
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
    op.execute(
        f"""
        CREATE TRIGGER trg_{TABLE}_append_only
            BEFORE UPDATE OR DELETE ON {SCHEMA}.{TABLE}
            FOR EACH ROW
            EXECUTE FUNCTION {SCHEMA}.forbid_append_only_mutation()
        """
    )


def downgrade() -> None:
    op.execute(f"DROP TRIGGER IF EXISTS trg_{TABLE}_append_only ON {SCHEMA}.{TABLE}")
    # The shared function is deliberately left in place: `einvoice_filings`, `ims_decisions`,
    # `gst_return_transitions`, `eway_bills` and `invoice_withholdings` still use it, and dropping
    # it here would disarm all of their triggers as a side effect of rolling back this slice.
    op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {SCHEMA}.{TABLE}")
    op.execute(f"ALTER TABLE {SCHEMA}.{TABLE} NO FORCE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {SCHEMA}.{TABLE} DISABLE ROW LEVEL SECURITY")
