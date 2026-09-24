"""ims_decision_pushes under RLS, with a guard that keeps the state machine a state machine

The guarantees for the table the previous migration added — and the one place this service
deliberately does **not** make a compliance table append-only.

**Row-level security**, `USING` *and* `WITH CHECK`, `ENABLE` *and* `FORCE`, for the reasons
`b62d9f4c17ae` gives: every row carries `organization_id`, and `WITH CHECK` is not optional because
`USING` is not applied to the new row of an INSERT — without it a tenant could queue a push
belonging to somebody else.

**A guard, not a freeze.** `einvoice_filings`, `eway_bills`, `ims_decisions` and `itc_ledger_entries`
refuse every UPDATE by trigger, because each row is a fact that happened. A push is not a fact, it is
delivery *state*: it has a lease, an attempt count and a backoff, and the single-flight guarantee
(two workers, one portal call per decision) is a conditional `UPDATE ... RETURNING`. That cannot
coexist with a trigger refusing updates. What must still be true, and what this guard makes true at
the database whatever the application does:

* **A push's identity never changes.** Which decision, which document, which period, which portal
  row, which action and version, under which key — and who created it and when. A push that could
  be re-pointed at another decision would let a `pushed` row vouch for a decision nobody sent.
* **A row is never deleted**, and never soft-deleted (`deleted_at` stays null): a superseded push is
  marked `superseded`, so "GSTN was never told about the first decision" stays provable.
* **`status` moves only along the machine's edges** — `queued → submitting | superseded | failed`,
  `submitting → queued | pushed | failed | conflict | superseded`, `failed → queued` — and
  `pushed`, `conflict` and `superseded` are terminal. A row cannot un-push itself.

Everything else (`attempts`, `lease_until`, the observed action, `drift`, …) is the state machine
doing its work and is free to change. Every transition writes its own audit row, and `audit_logs` is
the append-only half of the history.

Like the other guards this is a trigger and not a `REVOKE`: `migrations/env.py` runs
`apply_schema_grants` after every migration run, which re-grants UPDATE and DELETE on every table.

Revision ID: d63f0a1b8e25
Revises: 9b2e6a04d3c7
Create Date: 2026-09-24 11:05:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "d63f0a1b8e25"  # pragma: allowlist secret
down_revision: str | None = "9b2e6a04d3c7"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "finance"
APP_ROLE = "opsgrids_finance"
TABLE = "ims_decision_pushes"


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
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION {SCHEMA}.guard_ims_decision_push()
            RETURNS trigger
            LANGUAGE plpgsql
        AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION
                    '%.% rows are never deleted; a push that will not be sent is marked superseded',
                    TG_TABLE_SCHEMA, TG_TABLE_NAME
                    USING ERRCODE = 'restrict_violation';
            END IF;

            IF NEW.id IS DISTINCT FROM OLD.id
               OR NEW.organization_id IS DISTINCT FROM OLD.organization_id
               OR NEW.ims_document_id IS DISTINCT FROM OLD.ims_document_id
               OR NEW.decision_id IS DISTINCT FROM OLD.decision_id
               OR NEW.period IS DISTINCT FROM OLD.period
               OR NEW.source_ref IS DISTINCT FROM OLD.source_ref
               OR NEW.action IS DISTINCT FROM OLD.action
               OR NEW.version IS DISTINCT FROM OLD.version
               OR NEW.idempotency_key IS DISTINCT FROM OLD.idempotency_key
               OR NEW.created_at IS DISTINCT FROM OLD.created_at
               OR NEW.created_by IS DISTINCT FROM OLD.created_by
               OR NEW.deleted_at IS DISTINCT FROM OLD.deleted_at THEN
                RAISE EXCEPTION
                    'the identity of an IMS push is immutable; record a new decision instead'
                    USING ERRCODE = 'restrict_violation';
            END IF;

            IF NEW.status IS DISTINCT FROM OLD.status AND NOT (
                   (OLD.status = 'queued'     AND NEW.status IN ('submitting', 'superseded', 'failed'))
                OR (OLD.status = 'submitting' AND NEW.status IN
                        ('queued', 'pushed', 'failed', 'conflict', 'superseded'))
                OR (OLD.status = 'failed'     AND NEW.status = 'queued')
            ) THEN
                RAISE EXCEPTION 'an IMS push cannot move from % to %', OLD.status, NEW.status
                    USING ERRCODE = 'restrict_violation';
            END IF;

            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        f"""
        CREATE TRIGGER trg_{TABLE}_guard
            BEFORE UPDATE OR DELETE ON {SCHEMA}.{TABLE}
            FOR EACH ROW
            EXECUTE FUNCTION {SCHEMA}.guard_ims_decision_push()
        """
    )


def downgrade() -> None:
    op.execute(f"DROP TRIGGER IF EXISTS trg_{TABLE}_guard ON {SCHEMA}.{TABLE}")
    op.execute(f"DROP FUNCTION IF EXISTS {SCHEMA}.guard_ims_decision_push()")
    op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {SCHEMA}.{TABLE}")
    op.execute(f"ALTER TABLE {SCHEMA}.{TABLE} NO FORCE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {SCHEMA}.{TABLE} DISABLE ROW LEVEL SECURITY")
