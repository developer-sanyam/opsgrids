"""configurable stages: deal.pipelines and deal.pipeline_stages

R2.3 of the CRM overhaul (decisions D-5 and D-12). A deal's `stage` stays a plain key; what a key
*means* — its label, position, tone, default probability and its outcome (`open`, `won`, `lost`)
— moves from constants in code to rows: one pipeline per organization, with the tables shaped so
more can be added later.

**No data is written.** An organization with no `pipelines` row reads the default pipeline from
code (the six stages the service has always had), so nothing is seeded and nothing is backfilled;
the first stage-configuration write materialises the six rows under an advisory lock. This
revision only creates the tables.

* `pipelines` — at most one default pipeline per organization (`uq_pipelines_org_default`).
* `pipeline_stages` — keyed `(organization_id, pipeline_id, key)`; a key is never reused, even
  after its stage is archived. `pipeline_id` is an **organization-scoped** composite foreign key,
  so a stage cannot name another tenant's pipeline (the referential-integrity check itself
  bypasses row-level security). The database holds the row-local rules (key pattern, outcome,
  tone, the closing-probability pin, position) and freezes a stage's identity with a trigger;
  the rules that span rows (an active stage of each outcome, closed stages last, dense
  positions) are `app.pipeline.validate`, run under the exclusive pipeline lock.
* Both tables carry `organization_id`, are indexed on it and have row-level security enabled and
  forced, with the standard `tenant_isolation` policy. No grant is written here:
  `apply_schema_grants` re-grants DML on every table after each migration, so a stage's
  immutability is a **trigger**, not a revoke.

**Locking.** `lock_timeout` is 5s (fail the deploy, not the service). `migrations/env.py` also
sets that bound on the whole session, because the previous revision (`a1c9e2f7b3d4`) is
byte-pinned and takes its `ACCESS EXCLUSIVE` lock first, in the same transaction.

**Downgrade** drops everything this revision created, but **refuses** when an organization has
customised its pipeline (anything beyond the six untouched default rows) or when a deal sits in a
stage the previous code cannot name, unless `OPSGRIDS_ALLOW_DESTRUCTIVE_DOWNGRADE=1`: either would
orphan labels or strand a deal. Prefer rolling the code back — the old code ignores both tables.

Revision ID: f3b8d1a65c02
Revises: a1c9e2f7b3d4
Create Date: 2026-10-07 12:00:00.000000
"""

from __future__ import annotations

import os
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f3b8d1a65c02"  # pragma: allowlist secret
down_revision: str | None = "a1c9e2f7b3d4"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "deal"
APP_ROLE = "opsgrids_deal"

LOCK_TIMEOUT = "5s"
#: Set to `1` to let `downgrade()` destroy customised stages and strand deals.
ALLOW_DESTRUCTIVE_ENV = "OPSGRIDS_ALLOW_DESTRUCTIVE_DOWNGRADE"

#: The default pipeline as of this revision, copied rather than imported: a migration is a
#: snapshot and must not change when `app.pipeline` does. (key, label, position, outcome,
#: default probability, tone).
DEFAULT_STAGES = (
    ("lead", "Lead", 0, "open", 20, "info"),
    ("qualified", "Qualified", 1, "open", 40, "info"),
    ("proposal", "Proposal", 2, "open", 55, "info"),
    ("negotiation", "Negotiation", 3, "open", 70, "warning"),
    ("closed_won", "Closed Won", 4, "won", 100, "success"),
    ("closed_lost", "Closed Lost", 5, "lost", 0, "danger"),
)


def _rls(table: str) -> None:
    """Enable and FORCE row-level security and add the standard policy. `WITH CHECK` is not
    optional: USING gates existing rows and is not applied to the new row of an INSERT."""
    op.execute(f"ALTER TABLE {SCHEMA}.{table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {SCHEMA}.{table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY tenant_isolation ON {SCHEMA}.{table}
            FOR ALL TO {APP_ROLE}
            USING      (organization_id = {SCHEMA}.current_org())
            WITH CHECK (organization_id = {SCHEMA}.current_org())
        """
    )


def _unrls(table: str) -> None:
    op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {SCHEMA}.{table}")


def _tenant_columns() -> list[sa.Column]:
    """The `TenantBase` columns every business table carries (`platform_core.models`)."""
    return [
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column("updated_by", sa.String(length=64), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    ]


def upgrade() -> None:
    # Fail fast rather than queue: a lock request that waits behind a long transaction makes every
    # later query on that table wait behind *it*. (Local to this transaction.)
    op.execute(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'")

    # --- pipelines ---------------------------------------------------------------------------
    op.create_table(
        "pipelines",
        sa.Column("key", sa.String(length=40), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("is_default", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        *_tenant_columns(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_pipelines")),
        sa.UniqueConstraint("organization_id", "key", name="uq_pipelines_org_key"),
        # The target of `pipeline_stages`' organization-scoped foreign key.
        sa.UniqueConstraint("organization_id", "id", name="uq_pipelines_org_id"),
        schema=SCHEMA,
    )
    op.create_index(
        op.f("ix_deal_pipelines_organization_id"),
        "pipelines",
        ["organization_id"],
        unique=False,
        schema=SCHEMA,
    )
    op.create_index(
        "uq_pipelines_org_default",
        "pipelines",
        ["organization_id"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("is_default AND deleted_at IS NULL"),
    )
    _rls("pipelines")

    # --- pipeline_stages ---------------------------------------------------------------------
    op.create_table(
        "pipeline_stages",
        sa.Column("pipeline_id", sa.UUID(), nullable=False),
        sa.Column("key", sa.String(length=20), nullable=False),
        sa.Column("label", sa.String(length=80), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("outcome", sa.String(length=8), nullable=False),
        sa.Column("default_probability", sa.SmallInteger(), nullable=False),
        sa.Column("tone", sa.String(length=10), nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        *_tenant_columns(),
        sa.CheckConstraint("key ~ '^[a-z0-9_]{1,20}$'", name=op.f("ck_pipeline_stages_key")),
        sa.CheckConstraint(
            "outcome IN ('open', 'won', 'lost')", name=op.f("ck_pipeline_stages_outcome")
        ),
        sa.CheckConstraint(
            "default_probability BETWEEN 0 AND 100", name=op.f("ck_pipeline_stages_probability")
        ),
        sa.CheckConstraint(
            "outcome = 'open' "
            "OR (outcome = 'won' AND default_probability = 100) "
            "OR (outcome = 'lost' AND default_probability = 0)",
            name=op.f("ck_pipeline_stages_closing_pin"),
        ),
        sa.CheckConstraint(
            "tone IN ('neutral', 'info', 'success', 'warning', 'danger')",
            name=op.f("ck_pipeline_stages_tone"),
        ),
        sa.CheckConstraint("position >= 0", name=op.f("ck_pipeline_stages_position")),
        sa.ForeignKeyConstraint(
            ["organization_id", "pipeline_id"],
            [f"{SCHEMA}.pipelines.organization_id", f"{SCHEMA}.pipelines.id"],
            name="fk_pipeline_stages_pipeline",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_pipeline_stages")),
        # Not partial: a key is never reused, even after its stage is archived.
        sa.UniqueConstraint(
            "organization_id", "pipeline_id", "key", name="uq_pipeline_stages_org_pipeline_key"
        ),
        schema=SCHEMA,
    )
    op.create_index(
        op.f("ix_deal_pipeline_stages_organization_id"),
        "pipeline_stages",
        ["organization_id"],
        unique=False,
        schema=SCHEMA,
    )
    op.create_index(
        "ix_pipeline_stages_org_pipeline_position",
        "pipeline_stages",
        ["organization_id", "pipeline_id", "position"],
        unique=False,
        schema=SCHEMA,
    )
    # Active labels are unique case-insensitively, which is what lets an import resolve a label
    # to exactly one key. An archived stage may repeat an active label.
    op.create_index(
        "uq_pipeline_stages_org_pipeline_label_active",
        "pipeline_stages",
        ["organization_id", "pipeline_id", sa.literal_column("lower(label)")],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("archived_at IS NULL AND deleted_at IS NULL"),
    )
    _rls("pipeline_stages")

    # A stage's identity is frozen by the database, not by a revoke (the app role is re-granted
    # DML after every migration). `outcome` is frozen too: the API never changes one, and a
    # wrong outcome is archived and replaced, so the stronger rule costs nothing.
    op.execute(
        f"""
        CREATE FUNCTION {SCHEMA}.pipeline_stage_identity_frozen() RETURNS trigger
            LANGUAGE plpgsql
        AS $$
        BEGIN
            IF NEW.key IS DISTINCT FROM OLD.key
               OR NEW.pipeline_id IS DISTINCT FROM OLD.pipeline_id
               OR NEW.organization_id IS DISTINCT FROM OLD.organization_id
               OR NEW.outcome IS DISTINCT FROM OLD.outcome THEN
                RAISE EXCEPTION
                    'pipeline_stages: key, pipeline_id, organization_id and outcome are immutable';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        f"""
        CREATE TRIGGER trg_pipeline_stages_identity
            BEFORE UPDATE ON {SCHEMA}.pipeline_stages
            FOR EACH ROW EXECUTE FUNCTION {SCHEMA}.pipeline_stage_identity_frozen()
        """
    )


def _refuse_destructive_downgrade() -> None:
    """Dropping the tables destroys every label, order and tone an organization configured, and
    a deal in a stage the previous code cannot name would be stranded. Fine on a database that
    holds nothing beyond the six untouched default rows (in production, before R2.5, both
    tables are empty); never fine once anyone has configured a pipeline. Counts only: the
    message names no label and no deal."""
    if os.environ.get(ALLOW_DESTRUCTIVE_ENV) == "1":
        return
    bind = op.get_bind()
    defaults = ", ".join(
        "(" + ", ".join(f"'{v}'" if isinstance(v, str) else str(v) for v in row) + ")"
        for row in DEFAULT_STAGES
    )
    legacy_keys = ", ".join(f"'{row[0]}'" for row in DEFAULT_STAGES)
    customised_stages = bind.execute(
        sa.text(
            f"""
            WITH defaults(key, label, position, outcome, default_probability, tone) AS (
                VALUES {defaults}
            )
            SELECT count(*) FROM {SCHEMA}.pipeline_stages s
             WHERE s.archived_at IS NOT NULL
                OR s.deleted_at IS NOT NULL
                OR NOT EXISTS (
                    SELECT 1 FROM defaults d
                     WHERE d.key = s.key AND d.label = s.label AND d.position = s.position
                       AND d.outcome = s.outcome AND d.default_probability = s.default_probability
                       AND d.tone = s.tone)
            """  # noqa: S608 - the interpolated values are this file's own constants
        )
    ).scalar_one()
    customised_pipelines = bind.execute(
        sa.text(
            f"""
            SELECT count(*) FROM {SCHEMA}.pipelines
             WHERE NOT (key = 'sales' AND name = 'Sales pipeline'
                        AND is_default AND deleted_at IS NULL)
            """  # noqa: S608 - SCHEMA is this file's own constant
        )
    ).scalar_one()
    stranded_deals = bind.execute(
        sa.text(
            f"SELECT count(*) FROM {SCHEMA}.deals WHERE stage NOT IN ({legacy_keys})"  # noqa: S608
        )
    ).scalar_one()
    if customised_stages or customised_pipelines or stranded_deals:
        raise RuntimeError(
            f"Refusing to downgrade {revision}: {customised_stages} customised stage row(s), "
            f"{customised_pipelines} customised pipeline row(s) and {stranded_deals} deal(s) in "
            "a stage the previous code cannot name. Roll the application back instead (the "
            "schema is backward compatible: the old code ignores both tables). To destroy them "
            f"deliberately, after a backup, set {ALLOW_DESTRUCTIVE_ENV}=1."
        )


def downgrade() -> None:
    _refuse_destructive_downgrade()
    op.execute(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'")
    op.execute(f"DROP TRIGGER IF EXISTS trg_pipeline_stages_identity ON {SCHEMA}.pipeline_stages")
    op.execute(f"DROP FUNCTION IF EXISTS {SCHEMA}.pipeline_stage_identity_frozen()")
    _unrls("pipeline_stages")
    op.drop_table("pipeline_stages", schema=SCHEMA)
    _unrls("pipelines")
    op.drop_table("pipelines", schema=SCHEMA)
