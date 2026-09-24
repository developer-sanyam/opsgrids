# Dropping `employee.employees.membership_id` — a migration draft, held until the owner's dry-run

**Status: prepared, not scheduled, not applied. Run only after the owner's production dry-run.**
This is the migration [ADR-0008](0008-membership-employee-link.md) §3 promised ("a later ADR drops
the column") and its Decisions Q4 gated ("after one production dry-run reports zero"). R21 builds
the two things that make the gate checkable — `tools/verify_membership_links.py` and this draft —
and deliberately builds nothing that runs the drop.

**Nothing in this file is under `migrations/versions/`, and nothing loads it.** The draft is
Markdown so that no tool, no `alembic upgrade head`, no `make migrate` and no deploy can apply it
by accident. Moving it into `services/employee/migrations/versions/` is the act that arms it, and
that act belongs to a person, in a change of its own, after step 1 below.

## Why the drop waits

The column is the estate's only record of anybody's intent about the link (ADR-0008 §8), and it was
writable at create time before the §3 freeze. A production row could therefore hold a value that
came from an integration outside this repository. The reconcile (`tools/reconcile_membership_links.py`)
turns those into account's authoritative column; the verify pass proves it did, or shows what it
could not. Only when a **fleet-wide, read-only** run reports nothing left to fill, no conflict and
no unrecognised id is the column known to be redundant. Dropping it earlier destroys the one thing
the reconcile reads.

## The sequence — three steps, in order, and step 3 is a separate deploy

**1. The owner's production dry-run — the evidence.** From a checkout deployed to the box, with
`DATABASE_URL` pointing at production and `CI` unset:

```bash
uv run python tools/verify_membership_links.py --all > verify-membership-links.jsonl
echo "exit status: $?"
```

Read the last line. The drop may proceed only if **all** of these hold:

* the exit status is `0`;
* `column_drop_precondition.ready` is `true` (it is `true` only for `--all`, without `--apply`, with
  every organization clean);
* `fill_candidates_total`, `conflicts_total`, `unrecognised_employee_ids_total` and
  `unrecognised_membership_ids_total` are all `0`.

Attach the file to the change that arms the migration. If the run reports fill candidates, that is
the finding ADR-0008 §8 predicted ("somebody outside this repository was using the field"): review
them, run `--apply` **by hand** (it is refused when `CI` is set, audited per organization as
`verify-membership-links-cli`, and never overwrites), then repeat this step until it is clean.

**2. The code change that stops reading the column — first deploy, column still present.** The
RUNBOOK's rule for every migration is that the *old* code runs against the *new* schema for the
length of a deploy (`infrastructure/edge/RUNBOOK.md`, "Deploying", step 6). The ORM selects every
mapped column, so dropping `membership_id` while the previous `employee` image is still serving
would make every read fail. So the code lets go of the column one deploy before the database does:

* `services/employee/app/models.py` — remove `Employee.membership_id` and the
  `uq_employees_org_membership` index from the model (keep both in the database for now; the
  index's absence from the model is not a drift error because the migration in step 3 removes it,
  and `alembic check` is run after step 3, not between).
* `services/employee/app/schemas.py` — remove `EmployeeOut.membership_id`. **`EmployeeIn.membership_id`
  stays, and keeps refusing** with its pointer to `PATCH /api/v1/members/{membership_id}`
  (ADR-0008 Q4): removed outright, Pydantic's default would swallow the field and an integration
  that has been sending it would keep "succeeding" while doing nothing — the silence §3 rejected.
* `services/employee/app/services.py` — stop staging `membership_id` on `hrms.people.employee.onboarded`
  (it has been `null` since the §3 freeze); `libs/contracts/events/hrms.people.employee.onboarded`
  drops the key in a new schema version, or documents it as always absent — a contract decision, made
  in that change.
* `services/employee/app/api/internal.py` and `selectors.employee_links_stmt` —
  `/internal/v1/employee-links` returns employee ids only. The verify half of the reconcile keeps
  working (it needs only the set of ids); the fill half has nothing to key on and is removed with
  `--apply`'s fill.
* `frontend/product-hrms/src/api/types.ts` — remove `EmployeeOut.membership_id`.
* `docs/adr/0008-membership-employee-link.md` — record that the column is gone.

Deploy that. Confirm nothing reads the column: `grep -rn membership_id services/employee/app` returns
only `EmployeeIn`'s refusal.

**3. The migration — second deploy.** Below. It refuses to run without an attestation that steps 1
and 2 happened, prints what it is about to lose, and drops the index before the column.

## The precondition query

Run against the production `employee` schema before arming the migration. It is informational, not
a gate: a non-zero count is expected if step 1 filled links from employee's record, because after
the fill account holds every one of them. It says how many rows the drop discards.

```sql
-- How many employees still carry a membership_id (the values the drop will discard).
SELECT count(*) AS employees_with_membership_id,
       count(DISTINCT organization_id) AS organizations
FROM employee.employees
WHERE membership_id IS NOT NULL;

-- The index that goes with it, and what it covers.
SELECT indexname, indexdef
FROM pg_indexes
WHERE schemaname = 'employee'
  AND tablename = 'employees'
  AND indexname = 'uq_employees_org_membership';
```

The migration cannot check the thing that actually matters — that account holds the same links —
because a role that reads `employee.*` must not read `account.*` (ADR-0000 §10). That comparison is
step 1's report, and the attestation below is the operator saying they read it.

## The migration draft

Not a file in `migrations/versions/`. `down_revision` is the head of `services/employee` at the time
it is armed (at the time of writing: `89872c32de9e`, then whatever has landed since).

```python
"""drop employee.employees.membership_id (ADR-0008 Q4)

Run only after the owner's production dry-run reported nothing left to fill, no conflict and no
unrecognised id (docs/adr/0008-membership-id-column-drop.md, step 1), and only in a deploy AFTER the
one that stopped the code reading the column (step 2).

Revision ID: <generate>
Revises: <the employee head when armed>
"""

from __future__ import annotations

import os
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "<generate>"
down_revision: str | None = "<the employee head when armed>"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "employee"
TABLE = "employees"
INDEX = "uq_employees_org_membership"

#: Not a secret: a deliberate, greppable sentence that has to be typed to run this. `alembic upgrade
#: head` in a deploy will not have it set, so an armed-by-accident copy stops here.
ATTESTATION_ENV = "MEMBERSHIP_ID_DROP_ATTESTATION"
ATTESTATION = "owner-dry-run-verified"


def upgrade() -> None:
    if os.environ.get(ATTESTATION_ENV) != ATTESTATION:
        raise RuntimeError(
            "Refusing to drop employee.employees.membership_id: this needs the owner's production "
            "dry-run (tools/verify_membership_links.py --all reporting ready) and the code change "
            f"that stops reading the column. Set {ATTESTATION_ENV}={ATTESTATION} to confirm both."
        )

    bind = op.get_bind()
    discarded = bind.execute(
        sa.text(f"SELECT count(*) FROM {SCHEMA}.{TABLE} WHERE membership_id IS NOT NULL")
    ).scalar_one()
    print(f"dropping {SCHEMA}.{TABLE}.membership_id; {discarded} row(s) carry a value")

    op.drop_index(
        INDEX,
        table_name=TABLE,
        schema=SCHEMA,
        postgresql_where=sa.text("membership_id IS NOT NULL"),
    )
    op.drop_column(TABLE, "membership_id", schema=SCHEMA)


def downgrade() -> None:
    # The values are gone; the shape comes back so an older image can run. Every row reads null,
    # which is what the column has held since the ADR-0008 §3 freeze for every row written after it.
    op.add_column(
        TABLE, sa.Column("membership_id", sa.UUID(), nullable=True), schema=SCHEMA
    )
    op.create_index(
        INDEX,
        TABLE,
        ["organization_id", "membership_id"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("membership_id IS NOT NULL"),
    )
```

The same change, as the SQL a reviewer can read without Alembic:

```sql
DROP INDEX employee.uq_employees_org_membership;
ALTER TABLE employee.employees DROP COLUMN membership_id;
```

**It is not backward compatible with the previous image**, which is why step 2 comes first. It is
reversible in shape only — `downgrade()` restores an empty column and its index, not the values.
`make migration-roundtrip` on the armed migration proves the shape; nothing proves the values, which
is the whole reason for the gate.

## What is not decided here

* **Scheduling the verify pass.** ADR-0008 Q3 puts the nightly, report-only run inside the orphan
  check job R13 builds. That job does not exist yet; when it does, its entry is
  `python tools/verify_membership_links.py --all`, and an exit status of `3` is the alert. This
  document schedules nothing.
* **Whether to drop at all.** Q4 decided yes, after the dry-run. If the dry-run never comes back
  clean, the column stays, still writable by nothing.
