"""refunds and provider_events append-only

Two of payment's tables are the financial and reconciliation record, and both are append-only
(ADR-0000 §9):

* **`refunds`** — a refund is a fact that happened; a correction is a new row, never an edit.
* **`provider_events`** — the webhook inbox and the source of truth for reconciliation. The
  provider's own signed bytes, kept verbatim so a projection can always be replayed against them.
  A row is inserted once (idempotently, on `provider_event_id`) and never touched again.

**Why a trigger and not a `REVOKE`.** The tidy expression would be `REVOKE UPDATE, DELETE ... FROM
opsgrids_payment`. It does not hold on its own: `migrations/env.py` runs `apply_schema_grants` at the
end of every migration run, which `GRANT`s UPDATE/DELETE on *all* tables and only re-revokes
`audit_logs`. A revoke in a migration is therefore undone moments later, in the same `alembic
upgrade`. A `BEFORE UPDATE OR DELETE` trigger is independent of privileges: it fires whatever the
grant state, so append-only survives the post-migration re-grant — the same shape subscription's
transition table uses. The idempotent inbox insert (`ON CONFLICT DO NOTHING`) is unaffected: on a
conflict it performs no UPDATE, so the trigger never fires for a re-delivered webhook.

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-08-24 22:37:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = 'b2c3d4e5f6a7'  # pragma: allowlist secret
down_revision: str | None = 'a1b2c3d4e5f6'  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "payment"
APPEND_ONLY_TABLES = ("refunds", "provider_events")


def upgrade() -> None:
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
