"""one reversal per ledger entry, and no new lines under a finalized invoice

Two holes in the append-only money nets, both of the same shape: the *mutation* paths were
guarded and an *insert* path was not.

**Credit ledger.** `reverse_credit` refused reversing a reversal but never asked whether
the target already had one, and `ix_credit_ledger_org_reversal_of` was a plain index — so
a retried `POST /internal/v1/credit:reverse` (a bare internal POST, in a platform whose
delivery contract is at-least-once) inserted N negating rows and moved the balance by
N x amount. The index becomes a partial **unique** index: one reversal per entry, enforced
where the service check cannot reach — two racing transactions.

**Invoices.** The freeze trigger stops UPDATEs on a finalized invoice and the privilege
layer revokes UPDATE/DELETE on its lines, but INSERT on `invoice_lines` stayed granted
with nothing checking the parent's state. A stray script could append a line to a
finalized invoice and the frozen `total_minor` would silently stop footing. The new
`BEFORE INSERT` trigger refuses lines under an invoice that is not `draft` — **except**
inside the transaction that created the invoice row itself (`xmin` equals the current
transaction id), because this service legitimately writes an invoice already `finalized`
and appends its lines in the same transaction (models.py: there is no draft phase).
A later transaction appending to that invoice is exactly the corruption being refused.

Revision ID: c9d2e4f7a1b3
Revises: d1e8b4a06c37
Create Date: 2026-08-25 10:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "c9d2e4f7a1b3"  # pragma: allowlist secret
down_revision: str | None = "d1e8b4a06c37"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "billing"


def upgrade() -> None:
    op.drop_index("ix_credit_ledger_org_reversal_of", table_name="credit_ledger", schema=SCHEMA)
    op.create_index(
        "ix_credit_ledger_org_reversal_of",
        "credit_ledger",
        ["organization_id", "reversal_of"],
        unique=True,
        schema=SCHEMA,
        postgresql_where="reversal_of IS NOT NULL",
    )

    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION {SCHEMA}.forbid_line_under_frozen_invoice()
            RETURNS trigger
            LANGUAGE plpgsql
        AS $$
        DECLARE
            parent_state text;
            parent_xmin  xid;
        BEGIN
            SELECT state, xmin INTO parent_state, parent_xmin
                FROM {SCHEMA}.invoices
                WHERE id = NEW.invoice_id;
            IF parent_state IS NULL THEN
                -- No such invoice; the foreign key raises the honest error, not this.
                RETURN NEW;
            END IF;
            IF parent_state <> 'draft' AND parent_xmin <> pg_current_xact_id()::xid THEN
                RAISE EXCEPTION
                    'invoice % is % and frozen; its lines were written when it was, and '
                    'appending one now would break footing', NEW.invoice_id, parent_state
                    USING ERRCODE = 'restrict_violation';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        f"""
        CREATE TRIGGER trg_invoice_lines_no_late_insert
            BEFORE INSERT ON {SCHEMA}.invoice_lines
            FOR EACH ROW
            EXECUTE FUNCTION {SCHEMA}.forbid_line_under_frozen_invoice()
        """
    )


def downgrade() -> None:
    op.execute(
        f"DROP TRIGGER IF EXISTS trg_invoice_lines_no_late_insert ON {SCHEMA}.invoice_lines"
    )
    op.execute(f"DROP FUNCTION IF EXISTS {SCHEMA}.forbid_line_under_frozen_invoice()")

    op.drop_index("ix_credit_ledger_org_reversal_of", table_name="credit_ledger", schema=SCHEMA)
    op.create_index(
        "ix_credit_ledger_org_reversal_of",
        "credit_ledger",
        ["organization_id", "reversal_of"],
        schema=SCHEMA,
    )
