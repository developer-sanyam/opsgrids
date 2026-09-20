"""add hierarchy fields to employees

The employee half of ADR-0006 (the approval engine). `employee` is the only writer of
reporting lines, and this is what lets it publish full hierarchy state on every write and
refuse a change that would close a reporting cycle. See `docs/approval-engine-data-model.md`
§4 for the design this follows exactly, and `docs/adr/0006-approval-engine.md` §2 for why.

Four things land together, all additive, none backward-incompatible:

- `dotted_line_manager_id` — a second, informational reporting line. Composite FK on
  `(organization_id, dotted_line_manager_id)` so a manager from another tenant is
  unrepresentable, `NOT VALID` then `VALIDATE` to keep the lock short (the column is all-NULL
  at this point, so validation is instant). `ON DELETE SET NULL (dotted_line_manager_id)` —
  the PG15+ column-list form — nulls only the pointer, never `organization_id`.
- `hierarchy_version` — a monotonic counter approval's copy orders on, because `occurred_at`
  is a transaction-*start* timestamp and two concurrent edits can commit in the opposite order
  they started in (ADR-0006 §2, "Ordering the copy: a version, not a timestamp").
- `uq_employees_org_id` — the FK's target, and the index the backfill's keyset pagination
  (`WHERE organization_id = :org AND id > :cursor ORDER BY id`) is served by. Declared
  explicitly: the naming convention keys an unnamed unique on its first column only, and a
  future composite self-FK on `reporting_manager_id` would collide with an auto-generated name
  here (data model §1.0.1 rule 4).
- `trg_employees_hierarchy_version` — a `BEFORE UPDATE` trigger, not a service-side increment.
  `update_employee` and `transition_status` mutate ORM attributes on a row loaded by an earlier
  `SELECT`, and there is no `with_for_update` anywhere in `services/employee/app`, so a
  Python-side `+= 1` computes from a stale read: two concurrent edits both read the same value
  and both write the same next one, and approval's strictly-greater gate drops the second state
  permanently. The row lock the `UPDATE` itself takes serialises the trigger's increment, so it
  is monotonic with no application discipline required — including from a bulk import or a
  `psql` session, neither of which would remember. It also makes the column non-writable from
  outside: a client-supplied value is ignored.

Revision ID: 89872c32de9e
Revises: 6b37df855f2f
Create Date: 2026-09-16 05:11:07.359580
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "89872c32de9e"
down_revision: str | None = "6b37df855f2f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "employee"
TABLE = "employees"


def upgrade() -> None:
    # Metadata-only on PostgreSQL 16: a `NOT NULL DEFAULT <constant>` does not rewrite the
    # table, so this does not lock `employees` for the length of a backfill.
    op.add_column(
        TABLE, sa.Column("dotted_line_manager_id", sa.UUID(), nullable=True), schema=SCHEMA
    )
    op.add_column(
        TABLE,
        sa.Column(
            "hierarchy_version", sa.BigInteger(), nullable=False, server_default=sa.text("0")
        ),
        schema=SCHEMA,
    )

    op.create_check_constraint(
        op.f("ck_employees_hierarchy_version_non_negative"),
        TABLE,
        "hierarchy_version >= 0",
        schema=SCHEMA,
    )
    op.create_check_constraint(
        op.f("ck_employees_dotted_line_not_self"),
        TABLE,
        "dotted_line_manager_id IS NULL OR dotted_line_manager_id <> id",
        schema=SCHEMA,
    )
    op.create_unique_constraint(
        op.f("uq_employees_org_id"), TABLE, ["organization_id", "id"], schema=SCHEMA
    )

    # `NOT VALID` then `VALIDATE`: the column is all-NULL right now, so validation is instant,
    # but the two-step keeps the initial `ALTER TABLE ADD CONSTRAINT` from taking a lock for
    # longer than it takes to add the (unchecked) constraint.
    op.execute(
        f"""
        ALTER TABLE {SCHEMA}.{TABLE}
            ADD CONSTRAINT fk_employees_dotted_line_manager_id_employees
            FOREIGN KEY (organization_id, dotted_line_manager_id)
            REFERENCES {SCHEMA}.{TABLE} (organization_id, id)
            ON DELETE SET NULL (dotted_line_manager_id)
            NOT VALID
        """
    )
    op.execute(
        f"ALTER TABLE {SCHEMA}.{TABLE} "
        f"VALIDATE CONSTRAINT fk_employees_dotted_line_manager_id_employees"
    )

    # The version trigger. The compared tuple is exactly what `hierarchy_nodes` carries on
    # approval's copy (data model §4.2): a column that travels on the copy but is missing here
    # would change in employee and never reach approval; `department_id` is deliberately absent
    # for the same reason in reverse — it does not travel, so it must not bump the version.
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION {SCHEMA}.bump_hierarchy_version() RETURNS trigger
            LANGUAGE plpgsql AS $$
        BEGIN
          IF (NEW.reporting_manager_id, NEW.dotted_line_manager_id, NEW.status,
              NEW.full_name, NEW.employee_no, NEW.location_code)
             IS DISTINCT FROM
             (OLD.reporting_manager_id, OLD.dotted_line_manager_id, OLD.status,
              OLD.full_name, OLD.employee_no, OLD.location_code)
          THEN NEW.hierarchy_version := OLD.hierarchy_version + 1;
          ELSE NEW.hierarchy_version := OLD.hierarchy_version;
          END IF;
          RETURN NEW;
        END $$
        """
    )
    op.execute(
        f"""
        CREATE TRIGGER trg_employees_hierarchy_version
            BEFORE UPDATE ON {SCHEMA}.{TABLE}
            FOR EACH ROW EXECUTE FUNCTION {SCHEMA}.bump_hierarchy_version()
        """
    )


def downgrade() -> None:
    """Clean on an empty database. On a seeded one, destructive of `dotted_line_manager_id`
    values and version counters entered since deploy — approval would treat the next events as
    version 0 and drop them until versions climb past the stored ones. Prefer rolling forward;
    the faster incident lever is `ALTER ROLE opsgrids_employee BYPASSRLS`, not this."""
    op.execute(f"DROP TRIGGER IF EXISTS trg_employees_hierarchy_version ON {SCHEMA}.{TABLE}")
    op.execute(f"DROP FUNCTION IF EXISTS {SCHEMA}.bump_hierarchy_version()")

    op.execute(
        f"ALTER TABLE {SCHEMA}.{TABLE} "
        f"DROP CONSTRAINT IF EXISTS fk_employees_dotted_line_manager_id_employees"
    )
    op.drop_constraint(op.f("uq_employees_org_id"), TABLE, schema=SCHEMA, type_="unique")
    op.drop_constraint(
        op.f("ck_employees_dotted_line_not_self"), TABLE, schema=SCHEMA, type_="check"
    )
    op.drop_constraint(
        op.f("ck_employees_hierarchy_version_non_negative"), TABLE, schema=SCHEMA, type_="check"
    )

    op.drop_column(TABLE, "hierarchy_version", schema=SCHEMA)
    op.drop_column(TABLE, "dotted_line_manager_id", schema=SCHEMA)
