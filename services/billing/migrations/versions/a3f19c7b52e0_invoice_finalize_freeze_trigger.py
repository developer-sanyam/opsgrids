"""invoice finalize freeze trigger

A finalized invoice is the financial record a customer was billed against; re-pricing it, or
editing what it says, must be impossible below the application, not merely discouraged in it
(ADR-0000 §9, ADR-0004 §1). This service writes an invoice straight to `finalized`, so from the
first moment the row exists the only change the database permits is the single legal transition
`finalized -> void` — and even that may not touch the money or identity columns; voiding marks a
document uncollectible, it does not rewrite what was billed.

The service layer refuses an illegal change first, with a message a person can act on. This
`BEFORE UPDATE` trigger is the net under it: no code path — a stray script, a future endpoint, a
psql session — can quietly alter a finalized invoice. `DELETE` is handled by the privilege layer
(the RLS migration revokes it); the one thing that must never happen is a *silent change* to a
row somebody was billed against.

Revision ID: a3f19c7b52e0
Revises: fe5ef84271ed
Create Date: 2026-08-24 21:20:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = 'a3f19c7b52e0'  # pragma: allowlist secret
down_revision: str | None = 'fe5ef84271ed'  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "billing"


def upgrade() -> None:
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION {SCHEMA}.forbid_finalized_invoice_change()
            RETURNS trigger
            LANGUAGE plpgsql
        AS $$
        BEGIN
            -- A void invoice is terminal: nothing may change it.
            IF OLD.state = 'void' THEN
                RAISE EXCEPTION 'invoice % is void and immutable', OLD.id
                    USING ERRCODE = 'restrict_violation';
            END IF;

            IF OLD.state = 'finalized' THEN
                -- The one legal move: finalized -> void, and only the void-marking columns.
                IF NEW.state = 'void'
                   AND NEW.total_minor      IS NOT DISTINCT FROM OLD.total_minor
                   AND NEW.currency         IS NOT DISTINCT FROM OLD.currency
                   AND NEW.number           IS NOT DISTINCT FROM OLD.number
                   AND NEW.subscription_id  IS NOT DISTINCT FROM OLD.subscription_id
                   AND NEW.organization_id  IS NOT DISTINCT FROM OLD.organization_id
                   AND NEW.period_start     IS NOT DISTINCT FROM OLD.period_start
                   AND NEW.period_end       IS NOT DISTINCT FROM OLD.period_end
                   AND NEW.finalized_at     IS NOT DISTINCT FROM OLD.finalized_at
                THEN
                    RETURN NEW;
                END IF;

                RAISE EXCEPTION
                    'invoice % is finalized and immutable; only finalized->void is allowed, '
                    'and voiding must not alter its money or identity', OLD.id
                    USING ERRCODE = 'restrict_violation';
            END IF;

            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        f"""
        CREATE TRIGGER trg_invoices_freeze
            BEFORE UPDATE ON {SCHEMA}.invoices
            FOR EACH ROW
            EXECUTE FUNCTION {SCHEMA}.forbid_finalized_invoice_change()
        """
    )


def downgrade() -> None:
    op.execute(f"DROP TRIGGER IF EXISTS trg_invoices_freeze ON {SCHEMA}.invoices")
    op.execute(f"DROP FUNCTION IF EXISTS {SCHEMA}.forbid_finalized_invoice_change()")
