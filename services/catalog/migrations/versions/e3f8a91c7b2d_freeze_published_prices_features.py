"""freeze the prices and features of a published plan version, and its delete path

The immutability trigger on `plan_versions` protected the version *row* — state, name,
interval — but the money does not live there. `prices.amount_minor`,
`prices.unit_price_micros` and the entitlements in `features` carried no trigger and no
revoke, so a published, subscription-pinned price sheet could be UPDATEd or DELETEd below
the application while the "immutable" trigger looked on. Worse, `plans → plan_versions →
prices/features` is an `ON DELETE CASCADE` chain and DELETE was never guarded, so one
statement could silently remove a published price sheet entirely.

This closes both holes at the layer the guarantee is claimed at (the first trigger's own
docstring promises no stray script or psql session can quietly edit a frozen price):

- `BEFORE UPDATE OR DELETE` on `prices` and `features` raises when the parent version is
  `published` (or `grandfathered` — still pinned by the subscriptions that signed under
  it). A *draft*'s children stay freely editable, which is what `create_version`'s clone
  flow needs.
- `BEFORE DELETE` on `plan_versions` raises for the same states, which also stops the
  cascade from `plans`: deleting a plan that ever published now fails loudly instead of
  cascading a customer's pinned price sheet into nothing. (`retire_plan` is the supported
  path — it keeps every version, per ADR-0004 §2.)

Cascade ordering makes the child triggers safe: when a *draft* version is deleted, its
children are removed after the parent row, so the parent lookup finds nothing and the
delete proceeds. The guards fire only while a published parent still exists to protect.

Revision ID: e3f8a91c7b2d
Revises: c8811ff511d3
Create Date: 2026-08-25 09:30:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "e3f8a91c7b2d"  # pragma: allowlist secret
down_revision: str | None = "c8811ff511d3"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "catalog"

#: The two states a subscription can be pinned to. Draft is deliberately absent.
FROZEN_STATES = "('published', 'grandfathered')"

CHILD_TABLES = ("prices", "features")


def upgrade() -> None:
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION {SCHEMA}.forbid_frozen_version_child_mutation()
            RETURNS trigger
            LANGUAGE plpgsql
        AS $$
        DECLARE
            parent_state text;
        BEGIN
            SELECT state INTO parent_state
                FROM {SCHEMA}.plan_versions
                WHERE id = OLD.plan_version_id;
            IF parent_state IN {FROZEN_STATES} THEN
                RAISE EXCEPTION
                    'plan_version % is % and its % rows are immutable; clone into a new draft'
                    , OLD.plan_version_id
                    , parent_state
                    , TG_TABLE_NAME
                    USING ERRCODE = 'restrict_violation';
            END IF;
            IF TG_OP = 'DELETE' THEN
                RETURN OLD;
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    for table in CHILD_TABLES:
        op.execute(
            f"""
            CREATE TRIGGER trg_{table}_frozen_immutable
                BEFORE UPDATE OR DELETE ON {SCHEMA}.{table}
                FOR EACH ROW
                EXECUTE FUNCTION {SCHEMA}.forbid_frozen_version_child_mutation()
            """
        )

    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION {SCHEMA}.forbid_frozen_version_delete()
            RETURNS trigger
            LANGUAGE plpgsql
        AS $$
        BEGIN
            IF OLD.state IN {FROZEN_STATES} THEN
                RAISE EXCEPTION
                    'plan_version % is % and cannot be deleted; retire the plan instead'
                    , OLD.id
                    , OLD.state
                    USING ERRCODE = 'restrict_violation';
            END IF;
            RETURN OLD;
        END;
        $$
        """
    )
    op.execute(
        f"""
        CREATE TRIGGER trg_plan_versions_no_delete
            BEFORE DELETE ON {SCHEMA}.plan_versions
            FOR EACH ROW
            EXECUTE FUNCTION {SCHEMA}.forbid_frozen_version_delete()
        """
    )


def downgrade() -> None:
    op.execute(f"DROP TRIGGER IF EXISTS trg_plan_versions_no_delete ON {SCHEMA}.plan_versions")
    op.execute(f"DROP FUNCTION IF EXISTS {SCHEMA}.forbid_frozen_version_delete()")
    for table in CHILD_TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_frozen_immutable ON {SCHEMA}.{table}")
    op.execute(f"DROP FUNCTION IF EXISTS {SCHEMA}.forbid_frozen_version_child_mutation()")
