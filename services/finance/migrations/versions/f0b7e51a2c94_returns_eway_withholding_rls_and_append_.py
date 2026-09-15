"""returns, e-Way and withholding under RLS, with the append-only and snapshot guarantees

The guarantees for the five tables the previous migration added, and there are three of them.

**Row-level security**, on the four that carry tenant data — `gst_returns`,
`gst_return_transitions`, `eway_bills` and `invoice_withholdings`. Each gets the same
`tenant_isolation` policy every other finance table has: `USING` *and* `WITH CHECK`, `ENABLE`
*and* `FORCE`. `WITH CHECK` is not optional — `USING` is evaluated against existing rows and is
not applied to the new row of an INSERT, so without it a tenant could insert a return or an e-Way
bill belonging to somebody else: invisible to them, fully visible to the victim.

`withholding_rates` is the fifth and is deliberately **UNPOLICIED**, joining `tax_rates` and
`processed_events` on that list. It carries no `organization_id` because 2 % under GST s. 51 is
the law of one country, not one tenant's data, and a policy over a statute would be meaningless.

**Append-only on `gst_return_transitions`, `eway_bills` and `invoice_withholdings`**, by trigger.
The tidy expression would be `REVOKE UPDATE, DELETE ... FROM opsgrids_finance`, and it does not
hold: `migrations/env.py` runs `apply_schema_grants` at the end of every migration run, which
GRANTs UPDATE and DELETE on *all* tables in the schema and only re-revokes `audit_logs`. A revoke
written here is undone moments later, in the same `alembic upgrade`. The platform has now walked
into that on payment's `refunds`, on this service's `einvoice_filings` and on its `ims_decisions`,
so the trigger is not a stylistic preference — it is the only mechanism that survives the
re-grant. Each of the three holds **evidence**: when a return was filed and what it said, what
licence goods moved under, and what somebody else already withheld and paid to a tax authority on
this organization's behalf. Evidence the application can rewrite is not evidence.

**The snapshot freeze on `gst_returns`**, which is the one table here that is neither fully
mutable nor fully append-only, and the difference is the whole point. A return is a *record that
gets filed*, not a row that gets appended: `status`, `filed_at` and `arn` legitimately move
forward, while `sections` and the totals beside them are what was **reported** and must not change
afterwards. So a second trigger refuses an UPDATE that alters any snapshot column once `status` is
`filed`, and refuses DELETE outright. Without it, "an invoice corrected in September shows as a
difference against August" would be a convention the service happens to follow rather than
something the database will not let it break — and the difference between those two is exactly
what an auditor is asking about.

**The boundary is `filed`, not `generated`**, and that is deliberate rather than lax. Before
filing, nothing has been reported to anybody: the snapshot is this platform's own working answer,
and regenerating it after a late invoice arrives is not a rewrite of history, it is the *point* of
looking at a return before sending it. Freezing at `generated` would mean the first person to
click "generate" on the 1st had permanently fixed a period nobody had filed, which is a worse
failure than the one it prevents. Every regeneration still appends its own `gst_return_transitions`
row, so "this was assembled three times before it went" stays visible. Once the ARN comes back,
the snapshot is evidence and nothing may touch it.

Note what the frozen list includes: `deleted_at`. Setting it is still an UPDATE, and soft-deleting
a filed return would otherwise be the one remaining route to erasing what was reported.

Revision ID: f0b7e51a2c94
Revises: d4a1c93f6b28
Create Date: 2026-08-25 09:55:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "f0b7e51a2c94"  # pragma: allowlist secret
down_revision: str | None = "d4a1c93f6b28"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "finance"
APP_ROLE = "opsgrids_finance"

#: Tenant-scoped, so policied. `withholding_rates` is absent on purpose — see the docstring.
TENANT_TABLES = (
    "gst_returns",
    "gst_return_transitions",
    "eway_bills",
    "invoice_withholdings",
)

#: Written once, never touched again.
APPEND_ONLY_TABLES = ("gst_return_transitions", "eway_bills", "invoice_withholdings")

#: The columns that *are* the snapshot. Changing any of them after filing would rewrite what was
#: reported. `status`, `filed_at`, `arn`, `updated_at` and `updated_by` are deliberately absent: a
#: return moves forward through its lifecycle, and that movement is the point.
SNAPSHOT_COLUMNS = (
    "sections",
    "total_taxable_minor",
    "total_cgst_minor",
    "total_sgst_minor",
    "total_igst_minor",
    "document_count",
    "line_count",
    "generated_at",
    "period",
    "return_type",
    "gst_registration_id",
    "gstin",
    "state_code",
    "organization_id",
    # `deleted_at` is on the row because every tenant table carries it, and setting it is still an
    # UPDATE — so it is frozen here too. Otherwise soft-deleting a filed return would be the one
    # route to erasing what was reported, which is the whole thing this trigger is for.
    "deleted_at",
)


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

    # `finance.forbid_append_only_mutation()` already exists; two earlier migrations created it.
    # Created again with CREATE OR REPLACE so this migration stands on a database where either of
    # those has been rolled back, and so no downgrade leaves another's trigger pointing at a
    # function that is gone.
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

    # The snapshot freeze. `IS DISTINCT FROM` rather than `<>` so a NULL on either side compares
    # correctly — `sections` is NULL on a draft, and `<>` against NULL is NULL, which is not TRUE
    # and would let the one case that matters through.
    changed = " OR ".join(
        f"NEW.{column} IS DISTINCT FROM OLD.{column}" for column in SNAPSHOT_COLUMNS
    )
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION {SCHEMA}.forbid_filed_return_rewrite()
            RETURNS trigger
            LANGUAGE plpgsql
        AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION
                    'finance.gst_returns is not deletable; a return is the record of a tax period'
                    USING ERRCODE = 'restrict_violation';
            END IF;
            -- Nothing has been reported until it is filed, so a draft or a generated return may
            -- still be re-assembled. Every regeneration appends its own transition row.
            IF OLD.status <> 'filed' THEN
                RETURN NEW;
            END IF;
            IF {changed} THEN
                RAISE EXCEPTION
                    '%.% is frozen once filed; a later correction is an amendment in a later period, not a rewrite of this snapshot',
                    TG_TABLE_SCHEMA, TG_TABLE_NAME
                    USING ERRCODE = 'restrict_violation';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        f"""
        CREATE TRIGGER trg_gst_returns_snapshot_frozen
            BEFORE UPDATE OR DELETE ON {SCHEMA}.gst_returns
            FOR EACH ROW
            EXECUTE FUNCTION {SCHEMA}.forbid_filed_return_rewrite()
        """
    )


def downgrade() -> None:
    op.execute(f"DROP TRIGGER IF EXISTS trg_gst_returns_snapshot_frozen ON {SCHEMA}.gst_returns")
    op.execute(f"DROP FUNCTION IF EXISTS {SCHEMA}.forbid_filed_return_rewrite()")

    for table in APPEND_ONLY_TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_append_only ON {SCHEMA}.{table}")
    # The shared function is deliberately left in place: `einvoice_filings` and `ims_decisions`
    # still use it, and dropping it here would disarm their triggers as a side effect of rolling
    # back this slice.

    for table in TENANT_TABLES:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {SCHEMA}.{table}")
        op.execute(f"ALTER TABLE {SCHEMA}.{table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {SCHEMA}.{table} DISABLE ROW LEVEL SECURITY")
