"""partial unique shift/geofence codes

Retiring a shift or geo-fence (gap 11, soft `DELETE /shifts/{id}` and `DELETE /geofences/{id}`)
must not permanently burn its `code` / `location_code` — "I deleted it and now cannot recreate
it" is a support ticket waiting to happen. `uq_shifts_org_code` and `uq_geofences_org_location`
were plain unique constraints that counted soft-deleted rows, so this makes both partial unique
indexes on `deleted_at is null`: the constraint on `Shift`/`Geofence` becomes about the *live*
rows in an organization only. Duplicate detection in `services.create_shift`/`create_geofence`
already goes through `selectors.get_shift_by_code`/`get_geofence_by_location`, both of which
already filter `deleted_at IS NULL` — so app-level and index-level agree on what "duplicate"
means, before and after this migration.

Backward compatible with the running version: no column changes, and a partial unique index is
strictly less restrictive than the constraint it replaces (a soft-deleted row can no longer
collide), so a mid-deploy insert sees whichever version happens to be live without an error
either way.

Revision ID: f4c1a9b3d2e7
Revises: c08ce8789157
Create Date: 2026-09-21 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "f4c1a9b3d2e7"
down_revision: str | None = "c08ce8789157"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "attendance"


def upgrade() -> None:
    op.drop_constraint("uq_shifts_org_code", "shifts", schema=SCHEMA, type_="unique")
    op.create_index(
        "uq_shifts_org_code",
        "shifts",
        ["organization_id", "code"],
        unique=True,
        schema=SCHEMA,
        postgresql_where="deleted_at is null",
    )

    op.drop_constraint("uq_geofences_org_location", "geofences", schema=SCHEMA, type_="unique")
    op.create_index(
        "uq_geofences_org_location",
        "geofences",
        ["organization_id", "location_code"],
        unique=True,
        schema=SCHEMA,
        postgresql_where="deleted_at is null",
    )


def downgrade() -> None:
    op.drop_index("uq_geofences_org_location", table_name="geofences", schema=SCHEMA)
    op.create_unique_constraint(
        "uq_geofences_org_location",
        "geofences",
        ["organization_id", "location_code"],
        schema=SCHEMA,
    )

    op.drop_index("uq_shifts_org_code", table_name="shifts", schema=SCHEMA)
    op.create_unique_constraint(
        "uq_shifts_org_code",
        "shifts",
        ["organization_id", "code"],
        schema=SCHEMA,
    )
