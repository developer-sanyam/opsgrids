"""tenant collections: merchant accounts, a payee on every money table, status history (ADR-0024 A4)

A tenant's own customers paying the tenant, into the tenant's own Razorpay account — the opposite
direction to everything `payment` did before (OpsGrids collecting its own subscription charges).
ADR-0024 §8 puts it here, as `app/collections/`, rather than in a new service.

- **`merchant_accounts`** (new, tenant-scoped, forced RLS): the tenant's provider key id and its
  sealed key secret and webhook secret. One live row per organization (partial unique index on
  `deleted_at IS NULL`).
- **`payee`** (`platform` | `merchant`) on `charges`, `refunds` and `provider_events`. Added with
  a **server default**, so no `UPDATE` backfill runs — `refunds` and `provider_events` carry a
  `BEFORE UPDATE OR DELETE` trigger that would refuse one (ADR-0024 §8 names this exactly). A
  constant default on `ADD COLUMN` is a catalogue change on PostgreSQL 11+: no rewrite, no
  per-row update, the trigger never fires.
- **`charges`** gains `merchant_account_id` (intra-schema FK), `reference_type`/`reference_id` (a
  plain reference to what is being paid for — an operations order — never a cross-schema key), the
  two collection-only states `cancelled` and `expired`, CHECKs that keep a merchant charge and a
  platform charge from ever being mistaken for each other, and a partial unique index allowing at
  most one *open* collection per reference.
- **`refunds.idempotency_key`**, unique per organization where set: a retried refund request is
  the same refund.
- **`status_history`** (new, tenant-scoped, forced RLS, **append-only** by the same trigger
  function `refunds` and `provider_events` already use): every state a merchant charge or refund
  moved through, and what moved it.

Every existing row stays exactly what it was: a `platform` charge in one of its four states.

Downgrade **refuses** while any merchant data exists (a merchant account, a merchant charge,
refund or inbox row, or any status history) rather than guess what to do with a tenant's money
records — the same refusal shape ADR-0014's and ADR-0024 §5's migrations use.

Revision ID: a8c1d2e3f4b5
Revises: f6a7b8c9d1e2
Create Date: 2026-09-29 10:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a8c1d2e3f4b5"  # pragma: allowlist secret
down_revision: str | None = "f6a7b8c9d1e2"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "payment"
APP_ROLE = "opsgrids_payment"

#: The charge states before this migration, restored by the downgrade.
_OLD_STATES = "state in ('created', 'authorized', 'captured', 'failed')"
_NEW_STATES = "state in ('created', 'authorized', 'captured', 'failed', 'cancelled', 'expired')"


def _timestamps() -> list[sa.Column]:  # type: ignore[type-arg]
    return [
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column("updated_by", sa.String(length=64), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    ]


def _tenant_policy(table: str) -> None:
    """The same policy shape `a1b2c3d4e5f6` put on every other business table here: USING and
    WITH CHECK, forced, for the app role."""
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


def upgrade() -> None:
    # --- merchant_accounts --------------------------------------------------------------
    op.create_table(
        "merchant_accounts",
        sa.Column("provider", sa.String(length=40), nullable=False),
        sa.Column("key_id", sa.String(length=64), nullable=False),
        sa.Column("mode", sa.String(length=8), nullable=False),
        sa.Column("key_secret_enc", sa.LargeBinary(), nullable=True),
        sa.Column("key_secret_masked", sa.String(length=16), nullable=False),
        sa.Column("webhook_secret_enc", sa.LargeBinary(), nullable=True),
        sa.Column("webhook_secret_masked", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("rotated_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.CheckConstraint(
            "status in ('active', 'removed')", name=op.f("ck_merchant_accounts_status")
        ),
        sa.CheckConstraint("mode in ('test', 'live')", name=op.f("ck_merchant_accounts_mode")),
        sa.CheckConstraint(
            "(status = 'active') = (key_secret_enc IS NOT NULL AND webhook_secret_enc IS NOT NULL)",
            name=op.f("ck_merchant_accounts_secrets_present_iff_active"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_merchant_accounts")),
        schema=SCHEMA,
    )
    op.create_index(
        op.f("ix_payment_merchant_accounts_organization_id"),
        "merchant_accounts",
        ["organization_id"],
        unique=False,
        schema=SCHEMA,
    )
    op.create_index(
        "uq_merchant_accounts_org_live",
        "merchant_accounts",
        ["organization_id"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    _tenant_policy("merchant_accounts")

    # --- charges ------------------------------------------------------------------------
    op.add_column(
        "charges",
        sa.Column(
            "payee", sa.String(length=16), server_default=sa.text("'platform'"), nullable=False
        ),
        schema=SCHEMA,
    )
    op.add_column(
        "charges", sa.Column("merchant_account_id", sa.UUID(), nullable=True), schema=SCHEMA
    )
    op.add_column(
        "charges", sa.Column("reference_type", sa.String(length=40), nullable=True), schema=SCHEMA
    )
    op.add_column("charges", sa.Column("reference_id", sa.UUID(), nullable=True), schema=SCHEMA)
    op.create_foreign_key(
        "fk_charges_merchant_account_id",
        "charges",
        "merchant_accounts",
        ["merchant_account_id"],
        ["id"],
        source_schema=SCHEMA,
        referent_schema=SCHEMA,
    )
    op.drop_constraint(op.f("ck_charges_state"), "charges", schema=SCHEMA, type_="check")
    op.create_check_constraint(op.f("ck_charges_state"), "charges", _NEW_STATES, schema=SCHEMA)
    op.create_check_constraint(
        op.f("ck_charges_payee"), "charges", "payee in ('platform', 'merchant')", schema=SCHEMA
    )
    op.create_check_constraint(
        op.f("ck_charges_merchant_account_iff_merchant"),
        "charges",
        "(payee = 'merchant') = (merchant_account_id IS NOT NULL)",
        schema=SCHEMA,
    )
    op.create_check_constraint(
        op.f("ck_charges_merchant_charge_has_reference"),
        "charges",
        "payee = 'platform' OR (reference_type IS NOT NULL AND reference_id IS NOT NULL)",
        schema=SCHEMA,
    )
    op.create_check_constraint(
        op.f("ck_charges_platform_charge_states"),
        "charges",
        "payee = 'merchant' OR state in ('created', 'authorized', 'captured', 'failed')",
        schema=SCHEMA,
    )
    op.create_index(
        "ix_charges_org_reference",
        "charges",
        ["organization_id", "reference_type", "reference_id"],
        unique=False,
        schema=SCHEMA,
        postgresql_where=sa.text("reference_id IS NOT NULL"),
    )
    op.create_index(
        "uq_charges_open_collection",
        "charges",
        ["organization_id", "reference_type", "reference_id"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text(
            "payee = 'merchant' AND state IN ('created', 'authorized', 'failed')"
        ),
    )

    # --- refunds (append-only: columns added with defaults, never backfilled) -------------
    op.add_column(
        "refunds",
        sa.Column(
            "payee", sa.String(length=16), server_default=sa.text("'platform'"), nullable=False
        ),
        schema=SCHEMA,
    )
    op.add_column(
        "refunds", sa.Column("idempotency_key", sa.String(length=200), nullable=True), schema=SCHEMA
    )
    op.create_check_constraint(
        op.f("ck_refunds_payee"), "refunds", "payee in ('platform', 'merchant')", schema=SCHEMA
    )
    op.create_index(
        "uq_refunds_org_idempotency_key",
        "refunds",
        ["organization_id", "idempotency_key"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )

    # --- provider_events (append-only, same) ----------------------------------------------
    op.add_column(
        "provider_events",
        sa.Column(
            "payee", sa.String(length=16), server_default=sa.text("'platform'"), nullable=False
        ),
        schema=SCHEMA,
    )
    op.create_check_constraint(
        op.f("ck_provider_events_payee"),
        "provider_events",
        "payee in ('platform', 'merchant')",
        schema=SCHEMA,
    )

    # --- status_history -------------------------------------------------------------------
    op.create_table(
        "status_history",
        sa.Column("entity", sa.String(length=16), nullable=False),
        sa.Column("entity_id", sa.UUID(), nullable=False),
        sa.Column("from_state", sa.String(length=20), nullable=True),
        sa.Column("to_state", sa.String(length=20), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("provider_event_id", sa.String(length=120), nullable=True),
        sa.Column("reason", sa.String(length=64), nullable=True),
        sa.Column("at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        *_timestamps(),
        sa.CheckConstraint(
            "entity in ('charge', 'refund')", name=op.f("ck_status_history_entity")
        ),
        sa.CheckConstraint(
            "source in ('api', 'webhook', 'sync', 'sweep')", name=op.f("ck_status_history_source")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_status_history")),
        schema=SCHEMA,
    )
    op.create_index(
        op.f("ix_payment_status_history_organization_id"),
        "status_history",
        ["organization_id"],
        unique=False,
        schema=SCHEMA,
    )
    op.create_index(
        "ix_status_history_org_entity",
        "status_history",
        ["organization_id", "entity", "entity_id", "at"],
        unique=False,
        schema=SCHEMA,
    )
    _tenant_policy("status_history")
    # Append-only below the app role, by the trigger function `b2c3d4e5f6a7` created for
    # `refunds`/`provider_events` — independent of grants, so the post-migration re-grant in
    # env.py cannot undo it.
    op.execute(
        f"""
        CREATE TRIGGER trg_status_history_append_only
            BEFORE UPDATE OR DELETE ON {SCHEMA}.status_history
            FOR EACH ROW
            EXECUTE FUNCTION {SCHEMA}.forbid_append_only_mutation()
        """
    )


def downgrade() -> None:
    # Refuse rather than guess: a tenant's money records are not something a schema rollback
    # may silently drop. An operator who genuinely wants this rolls the data out by hand first.
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM {SCHEMA}.merchant_accounts)
               OR EXISTS (SELECT 1 FROM {SCHEMA}.charges WHERE payee <> 'platform')
               OR EXISTS (SELECT 1 FROM {SCHEMA}.refunds WHERE payee <> 'platform')
               OR EXISTS (SELECT 1 FROM {SCHEMA}.provider_events WHERE payee <> 'platform')
               OR EXISTS (SELECT 1 FROM {SCHEMA}.status_history)
            THEN
                RAISE EXCEPTION
                    'payment holds tenant-collection data; refusing to drop it in a downgrade'
                    USING ERRCODE = 'restrict_violation';
            END IF;
        END
        $$
        """
    )

    op.execute(f"DROP TRIGGER IF EXISTS trg_status_history_append_only ON {SCHEMA}.status_history")
    op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {SCHEMA}.status_history")
    op.drop_index("ix_status_history_org_entity", table_name="status_history", schema=SCHEMA)
    op.drop_index(
        op.f("ix_payment_status_history_organization_id"),
        table_name="status_history",
        schema=SCHEMA,
    )
    op.drop_table("status_history", schema=SCHEMA)

    op.drop_constraint(
        op.f("ck_provider_events_payee"), "provider_events", schema=SCHEMA, type_="check"
    )
    op.drop_column("provider_events", "payee", schema=SCHEMA)

    op.drop_index("uq_refunds_org_idempotency_key", table_name="refunds", schema=SCHEMA)
    op.drop_constraint(op.f("ck_refunds_payee"), "refunds", schema=SCHEMA, type_="check")
    op.drop_column("refunds", "idempotency_key", schema=SCHEMA)
    op.drop_column("refunds", "payee", schema=SCHEMA)

    op.drop_index("uq_charges_open_collection", table_name="charges", schema=SCHEMA)
    op.drop_index("ix_charges_org_reference", table_name="charges", schema=SCHEMA)
    for name in (
        "ck_charges_platform_charge_states",
        "ck_charges_merchant_charge_has_reference",
        "ck_charges_merchant_account_iff_merchant",
        "ck_charges_payee",
        "ck_charges_state",
    ):
        op.drop_constraint(op.f(name), "charges", schema=SCHEMA, type_="check")
    op.create_check_constraint(op.f("ck_charges_state"), "charges", _OLD_STATES, schema=SCHEMA)
    op.drop_constraint("fk_charges_merchant_account_id", "charges", schema=SCHEMA, type_="foreignkey")
    op.drop_column("charges", "reference_id", schema=SCHEMA)
    op.drop_column("charges", "reference_type", schema=SCHEMA)
    op.drop_column("charges", "merchant_account_id", schema=SCHEMA)
    op.drop_column("charges", "payee", schema=SCHEMA)

    op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {SCHEMA}.merchant_accounts")
    op.drop_index("uq_merchant_accounts_org_live", table_name="merchant_accounts", schema=SCHEMA)
    op.drop_index(
        op.f("ix_payment_merchant_accounts_organization_id"),
        table_name="merchant_accounts",
        schema=SCHEMA,
    )
    op.drop_table("merchant_accounts", schema=SCHEMA)
