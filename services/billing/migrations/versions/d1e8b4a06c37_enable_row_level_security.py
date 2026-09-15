"""enable row level security and lock the ledger append-only

Tenant isolation stops being a convention every query has to remember and becomes something the
database enforces, exactly as on the `account`, `product`, `employee` and `catalog` schemas
(ADR-0002). The API connects as `opsgrids_billing` (NOBYPASSRLS), the migrations as the admin
role, and `get_tenant_session` / the internal API bind the tenant GUC as the first statement of
every request transaction.

Two things beyond isolation are enforced here, because "append-only" and "frozen" are privilege
and trigger facts, not application promises (ADR-0000 §9):

* **`invoice_lines` and `credit_ledger` are append-only.** `apply_schema_grants` auto-revokes
  UPDATE/DELETE on `audit_logs` only, so this migration revokes UPDATE and DELETE on those two
  from the app role. A correction is a new invoice or a new reversing ledger row — never an edit.
* **`invoices` is frozen after finalize.** DELETE is revoked outright. UPDATE is *not* revoked —
  the one legal transition `finalized -> void` is an app write — but the freeze trigger (previous
  migration) permits only that move and refuses every other change, so retaining UPDATE does not
  reopen the invoice. (This is the deliberate reading of the brief: void is modelled as a state
  move, which needs UPDATE, so the immutability of a finalized invoice is carried by the trigger
  and by DELETE being revoked, not by revoking UPDATE.)

`processed_events` has no organization column, so any policy on it would silently defeat consumer
idempotency; `tax_rates` is global platform reference data (a statutory rate is the same fact for
every tenant, like `catalog.plans`) — both are deliberately un-policied (ADR-0002 §5).

Revision ID: d1e8b4a06c37
Revises: a3f19c7b52e0
Create Date: 2026-08-24 21:24:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = 'd1e8b4a06c37'  # pragma: allowlist secret
down_revision: str | None = 'a3f19c7b52e0'  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "billing"
APP_ROLE = "opsgrids_billing"

#: Tenant-scoped. Every one carries `organization_id` — the two platform tables every service
#: has, plus billing's three financial tables.
TENANT_TABLES = ("audit_logs", "credit_ledger", "invoice_lines", "invoices", "outbox")

#: Deliberately **not** policied. `processed_events` has no organization column (a policy would
#: silently defeat idempotency); `tax_rates` is global platform reference data (ADR-0002 §5).
UNPOLICIED = ("processed_events", "tax_rates")

#: Append-only, enforced by privilege rather than by convention. `audit_logs` is already revoked
#: by `apply_schema_grants`; these two are billing's own. `invoices` keeps UPDATE (for the
#: finalized->void move, guarded by the freeze trigger) and loses only DELETE.
APPEND_ONLY = ("credit_ledger", "invoice_lines")


def _enable(table: str) -> None:
    op.execute(f"ALTER TABLE {SCHEMA}.{table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {SCHEMA}.{table} FORCE ROW LEVEL SECURITY")


def upgrade() -> None:
    # nullif(..., '') because a transaction-local GUC reads back as '' once the setting
    # transaction has ended; without it '' ::uuid raises, with it the predicate is simply false
    # and the query returns nothing — fail-quiet, with the application asserting loudly instead
    # (ADR-0002 §4).
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION {SCHEMA}.current_org() RETURNS uuid
            LANGUAGE sql STABLE PARALLEL SAFE
        AS $$ SELECT nullif(current_setting('app.organization_id', true), '')::uuid $$
        """
    )

    for table in TENANT_TABLES:
        _enable(table)
        # WITH CHECK is not optional: USING is not applied to the new row of an INSERT, so
        # without it a tenant could insert a row belonging to somebody else.
        op.execute(
            f"""
            CREATE POLICY tenant_isolation ON {SCHEMA}.{table}
                FOR ALL TO {APP_ROLE}
                USING      (organization_id = {SCHEMA}.current_org())
                WITH CHECK (organization_id = {SCHEMA}.current_org())
            """
        )

    # The financial record is append-only / frozen below the application.
    for table in APPEND_ONLY:
        op.execute(f'REVOKE UPDATE, DELETE ON "{SCHEMA}"."{table}" FROM "{APP_ROLE}"')
    # A finalized invoice may still transition to void (an UPDATE the freeze trigger gates), so
    # only DELETE is revoked here — the trigger carries immutability, the privilege stops removal.
    op.execute(f'REVOKE DELETE ON "{SCHEMA}"."invoices" FROM "{APP_ROLE}"')


def downgrade() -> None:
    # Restore the privileges first, then drop the policies and the helper.
    op.execute(f'GRANT DELETE ON "{SCHEMA}"."invoices" TO "{APP_ROLE}"')
    for table in APPEND_ONLY:
        op.execute(f'GRANT UPDATE, DELETE ON "{SCHEMA}"."{table}" TO "{APP_ROLE}"')

    for table in TENANT_TABLES:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {SCHEMA}.{table}")
        op.execute(f"ALTER TABLE {SCHEMA}.{table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {SCHEMA}.{table} DISABLE ROW LEVEL SECURITY")

    op.execute(f"DROP FUNCTION IF EXISTS {SCHEMA}.current_org()")
