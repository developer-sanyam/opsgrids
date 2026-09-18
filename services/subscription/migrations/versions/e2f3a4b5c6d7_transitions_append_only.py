"""subscription_transitions append-only

The transition stream is the audit of the commercial lifecycle and the source the operator console
rebuilds MRR, trial-conversion and churn from (ADR-0004 §4). A stream you can edit or delete after
the fact is not one you can defend, so append-only is enforced at the database — a correction is a
new row (a later transition), never a mutation of an old one.

**Why a trigger and not a `REVOKE`.** The tidy expression of append-only would be `REVOKE UPDATE,
DELETE ... FROM opsgrids_subscription`. It does not hold: `migrations/env.py` runs
`apply_schema_grants` at the end of every migration run, which `GRANT`s UPDATE/DELETE on *all*
tables in the schema and only re-revokes them on `audit_logs`. A revoke in a migration is therefore
undone moments later, in the same `alembic upgrade` — verified against this database, not assumed. A
`BEFORE UPDATE OR DELETE` trigger is independent of privileges: it fires whatever the grant state, so
append-only survives the post-migration re-grant, and it raises the same way catalog's immutability
trigger does. The service layer never issues an UPDATE or DELETE here; this is the net under that.

Revision ID: e2f3a4b5c6d7
Revises: b7e1a2c3d4f5
Create Date: 2026-08-24 20:57:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = 'e2f3a4b5c6d7'  # pragma: allowlist secret
down_revision: str | None = 'b7e1a2c3d4f5'  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "subscription"
TABLE = "subscription_transitions"


def upgrade() -> None:
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION {SCHEMA}.forbid_transition_mutation()
            RETURNS trigger
            LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION
                'subscription_transitions is append-only; a correction is a new transition row'
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
            EXECUTE FUNCTION {SCHEMA}.forbid_transition_mutation()
        """
    )


def downgrade() -> None:
    op.execute(f"DROP TRIGGER IF EXISTS trg_{TABLE}_append_only ON {SCHEMA}.{TABLE}")
    op.execute(f"DROP FUNCTION IF EXISTS {SCHEMA}.forbid_transition_mutation()")
