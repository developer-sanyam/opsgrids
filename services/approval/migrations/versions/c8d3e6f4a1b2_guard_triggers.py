"""guard triggers — append-only steps, revoke-only delegations, decide-once requests

Three triggers, the durable enforcement `REVOKE` alone cannot provide (data model §1.4's
"Append-only enforcement"): `migrations/env.py` runs `apply_schema_grants` after **every**
migration run, which re-`GRANT`s `UPDATE`/`DELETE` on every table in the schema and only
re-revokes them on `audit_logs`. A `REVOKE` written into this migration would therefore be undone
moments later by the next `alembic upgrade`. A `BEFORE UPDATE OR DELETE` trigger is independent of
privileges — it fires whatever the grant state is — the same shape
`subscription`'s `trg_subscription_transitions_append_only` already uses.

The three bodies are transcribed verbatim from `docs/approval-engine-data-model.md` §1.4 (append
only, revoke only) and its `trg_approval_requests_decide_once` (which is already written in its
*amended* form here — the write-once `application_*` trio permitted on a decided request from the
day this table exists, ADR-0006 §12 — there is no earlier, narrower version to migrate away from
in this codebase, unlike the persistence design's own before/after framing).

`updated_at`/`updated_by` are deliberately absent from every frozen-tuple comparison below: the
ORM bumps them on any flush, so including them would make every legal, no-op-looking `UPDATE`
(the ORM re-saving a row it did not actually change) look like a mutation attempt.

Revision ID: c8d3e6f4a1b2
Revises: b1c4d7e29f3a
Create Date: 2026-09-16 11:45:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = 'c8d3e6f4a1b2'
down_revision: str | None = 'b1c4d7e29f3a'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "approval"

_STEP_GUARD_FN = f"""
CREATE OR REPLACE FUNCTION {SCHEMA}.guard_approval_step_change() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'approval_steps is append-only; step % cannot be deleted', OLD.id
      USING ERRCODE = 'restrict_violation';
  END IF;
  IF OLD.outcome <> 'open' THEN
    RAISE EXCEPTION 'approval step % is % and immutable', OLD.id, OLD.outcome
      USING ERRCODE = 'restrict_violation';
  END IF;
  IF NEW.outcome = 'open'
     OR (NEW.id, NEW.organization_id, NEW.request_id, NEW.level, NEW.approver_employee_id,
         NEW.acting_for_employee_id, NEW.delegation_id, NEW.reason, NEW.opened_at, NEW.due_at,
         NEW.created_at, NEW.created_by, NEW.deleted_at)
        IS DISTINCT FROM
        (OLD.id, OLD.organization_id, OLD.request_id, OLD.level, OLD.approver_employee_id,
         OLD.acting_for_employee_id, OLD.delegation_id, OLD.reason, OLD.opened_at, OLD.due_at,
         OLD.created_at, OLD.created_by, OLD.deleted_at)
  THEN
    RAISE EXCEPTION 'approval step %: the only permitted change is closing an open step (outcome, closed_at)',
      OLD.id USING ERRCODE = 'restrict_violation';
  END IF;
  RETURN NEW;
END $$;
"""

#: The data model gives this one's behaviour in prose rather than as literal SQL (§1.4: "refuses
#: DELETE, allows only revoked_at/revoked_by_employee_id to be set, once. An extend-the-window
#: update and an un-revoke both fail") — the body below is written to match: everything except
#: the revocation pair is frozen (closes the window-extension hole), and a second write to
#: `revoked_at` once it is non-NULL is refused (closes both re-revoking and un-revoking).
_DELEGATION_GUARD_FN = f"""
CREATE OR REPLACE FUNCTION {SCHEMA}.guard_delegation_change() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'delegations cannot be deleted; delegation % is revoked, not removed', OLD.id
      USING ERRCODE = 'restrict_violation';
  END IF;
  IF (NEW.id, NEW.organization_id, NEW.delegator_employee_id, NEW.delegate_employee_id,
      NEW.starts_at, NEW.ends_at, NEW.created_by_employee_id,
      NEW.created_at, NEW.created_by, NEW.deleted_at)
     IS DISTINCT FROM
     (OLD.id, OLD.organization_id, OLD.delegator_employee_id, OLD.delegate_employee_id,
      OLD.starts_at, OLD.ends_at, OLD.created_by_employee_id,
      OLD.created_at, OLD.created_by, OLD.deleted_at)
  THEN
    RAISE EXCEPTION 'delegation %: only revoked_at and revoked_by_employee_id may be set', OLD.id
      USING ERRCODE = 'restrict_violation';
  END IF;
  IF OLD.revoked_at IS NOT NULL AND NEW.revoked_at IS DISTINCT FROM OLD.revoked_at THEN
    RAISE EXCEPTION 'delegation % is already revoked', OLD.id
      USING ERRCODE = 'restrict_violation';
  END IF;
  RETURN NEW;
END $$;
"""

_REQUEST_GUARD_FN = f"""
CREATE OR REPLACE FUNCTION {SCHEMA}.guard_approval_request_change() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'approval_requests is not deletable; request %', OLD.id
      USING ERRCODE = 'restrict_violation';
  END IF;

  IF OLD.status = 'pending' THEN
    IF (NEW.id, NEW.organization_id, NEW.request_type, NEW.source_ref, NEW.requester_employee_id,
        NEW.summary, NEW.request_note, NEW.opened_at, NEW.created_at, NEW.created_by, NEW.deleted_at)
       IS DISTINCT FROM
       (OLD.id, OLD.organization_id, OLD.request_type, OLD.source_ref, OLD.requester_employee_id,
        OLD.summary, OLD.request_note, OLD.opened_at, OLD.created_at, OLD.created_by, OLD.deleted_at)
    THEN
      RAISE EXCEPTION 'approval request %: identity, summary, request_note and opened_at are frozen', OLD.id
        USING ERRCODE = 'restrict_violation';
    END IF;
    IF NEW.application_state IS NOT NULL THEN
      RAISE EXCEPTION 'approval request %: application cannot be reported before a decision', OLD.id
        USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN NEW;
  END IF;

  -- OLD is decided: the only permitted change is the write-once application trio.
  IF OLD.application_state IS NOT NULL THEN
    RAISE EXCEPTION 'approval request %: application already reported as %', OLD.id, OLD.application_state
      USING ERRCODE = 'restrict_violation';
  END IF;
  IF NEW.application_state IS NULL THEN
    RAISE EXCEPTION 'approval request % is decided and immutable', OLD.id
      USING ERRCODE = 'restrict_violation';
  END IF;
  IF (NEW.id, NEW.organization_id, NEW.request_type, NEW.source_ref, NEW.requester_employee_id,
      NEW.summary, NEW.request_note, NEW.status, NEW.opened_at, NEW.decided_by_employee_id,
      NEW.acting_for_employee_id, NEW.decided_at, NEW.comment,
      NEW.created_at, NEW.created_by, NEW.deleted_at)
     IS DISTINCT FROM
     (OLD.id, OLD.organization_id, OLD.request_type, OLD.source_ref, OLD.requester_employee_id,
      OLD.summary, OLD.request_note, OLD.status, OLD.opened_at, OLD.decided_by_employee_id,
      OLD.acting_for_employee_id, OLD.decided_at, OLD.comment,
      OLD.created_at, OLD.created_by, OLD.deleted_at)
  THEN
    RAISE EXCEPTION 'approval request %: only application_state, application_failure_reason and applied_at may be set after a decision', OLD.id
      USING ERRCODE = 'restrict_violation';
  END IF;
  RETURN NEW;
END $$;
"""


def upgrade() -> None:
    op.execute(_STEP_GUARD_FN)
    op.execute(
        f"""
        CREATE TRIGGER trg_approval_steps_append_only
            BEFORE UPDATE OR DELETE ON {SCHEMA}.approval_steps
            FOR EACH ROW EXECUTE FUNCTION {SCHEMA}.guard_approval_step_change()
        """
    )

    op.execute(_DELEGATION_GUARD_FN)
    op.execute(
        f"""
        CREATE TRIGGER trg_delegations_revoke_only
            BEFORE UPDATE OR DELETE ON {SCHEMA}.delegations
            FOR EACH ROW EXECUTE FUNCTION {SCHEMA}.guard_delegation_change()
        """
    )

    op.execute(_REQUEST_GUARD_FN)
    op.execute(
        f"""
        CREATE TRIGGER trg_approval_requests_decide_once
            BEFORE UPDATE OR DELETE ON {SCHEMA}.approval_requests
            FOR EACH ROW EXECUTE FUNCTION {SCHEMA}.guard_approval_request_change()
        """
    )


def downgrade() -> None:
    op.execute(f"DROP TRIGGER IF EXISTS trg_approval_requests_decide_once ON {SCHEMA}.approval_requests")
    op.execute(f"DROP FUNCTION IF EXISTS {SCHEMA}.guard_approval_request_change()")

    op.execute(f"DROP TRIGGER IF EXISTS trg_delegations_revoke_only ON {SCHEMA}.delegations")
    op.execute(f"DROP FUNCTION IF EXISTS {SCHEMA}.guard_delegation_change()")

    op.execute(f"DROP TRIGGER IF EXISTS trg_approval_steps_append_only ON {SCHEMA}.approval_steps")
    op.execute(f"DROP FUNCTION IF EXISTS {SCHEMA}.guard_approval_step_change()")
