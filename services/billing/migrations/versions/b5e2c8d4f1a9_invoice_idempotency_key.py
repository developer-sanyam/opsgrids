"""one invoice per subscription period: a platform idempotency key on invoices

The billing run (subscription-service's sweep, ADR-0005 addendum 2026-09-23) asks billing to
invoice one subscription for one period, over HTTP, with at-least-once semantics: a sweep that
crashes after billing committed and before subscription recorded the answer will ask again, and
two sweep replicas can ask at the same moment. Each ask must land on the **same** invoice.

The key is `subscription:{subscription_id}:{period_start}` — built by billing from the request,
never taken from the caller as free text — and the index is **partial and unique** on
`(organization_id, idempotency_key)`: an explicit, hand-finalized invoice carries no key (NULL)
and is unaffected, while a second billing-run insert for the same period is refused by the
database, below any check the service makes first. The insert is `ON CONFLICT DO NOTHING`
against exactly this index, so the loser of a race reads the winner's row instead of raising.

The freeze trigger is re-created so that the one legal post-finalize move, `finalized -> void`,
may not alter the key either. A void invoice keeps its key: re-running the period returns the
voided document rather than silently issuing a second one, and a correction stays what
ADR-0000 §9 says it is — a new, explicit invoice.

Revision ID: b5e2c8d4f1a9
Revises: adc4d9aefbdb
Create Date: 2026-09-23 12:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b5e2c8d4f1a9"  # pragma: allowlist secret
down_revision: str | None = "adc4d9aefbdb"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "billing"


def _freeze_function(*, with_key: bool) -> str:
    key_clause = (
        "AND NEW.idempotency_key  IS NOT DISTINCT FROM OLD.idempotency_key" if with_key else ""
    )
    return f"""
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
                   {key_clause}
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


def upgrade() -> None:
    op.add_column(
        "invoices",
        sa.Column("idempotency_key", sa.String(length=200), nullable=True),
        schema=SCHEMA,
    )
    op.create_index(
        "uq_invoices_org_idempotency_key",
        "invoices",
        ["organization_id", "idempotency_key"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )
    op.execute(_freeze_function(with_key=True))


def downgrade() -> None:
    op.execute(_freeze_function(with_key=False))
    op.drop_index(
        "uq_invoices_org_idempotency_key",
        table_name="invoices",
        schema=SCHEMA,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )
    op.drop_column("invoices", "idempotency_key", schema=SCHEMA)
