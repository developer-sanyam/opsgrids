"""consent ledger

`consent_events`: the append-only record of who opted in or out of what, when, and through which
source — the table `app/consent.py` decides from, so that one function, not two disjoint stores,
answers "may we send this?" for every send path (see that module and `ConsentEvent`'s docstring).

The address is PII, so it is stored the way `suppressions` and `contacts` store it: sealed
(`subject_enc`), masked (`subject_masked`) and blind-indexed (`subject_index`), with no plaintext
column anywhere in the table.

**Append-only is enforced by the database, not by convention.** `UPDATE` and `DELETE` are revoked
from the application role here, and `migrations/env.py` re-applies the revoke after every run of
`apply_schema_grants` (which re-grants both on every table), the same shape as the console's
`revenue_events`. A change of mind is a new row.

Row-level security is hand-written in the shape of every other tenant table in this schema:
`ENABLE` and `FORCE`, with `WITH CHECK` as well as `USING` so one tenant cannot insert a row into
another's ledger.

`messages.suppression_reason` is the one other column: the machine-readable reason a message was
suppressed (`consent:no_opt_in`, ...). Nullable, so adding it rewrites nothing.

**Backfill, in this migration.** Without it the new decision would refuse every existing tenant's
campaigns on the first send: marketing needs an opt-in event and none exist. So the upgrade calls
`app.consent_backfill.backfill_legacy_consent`, which carries every existing `Preference` refusal
and every `Suppression` in as `opted_out`, and every active unsuppressed contact in as an
`opted_in` email/marketing event with `source = legacy_import` — **unverified consent**, marked
as such in its evidence — so existing campaigns select the same recipients. It is idempotent
(deterministic `idempotency_key`s), and can be run again by hand (`python -m app.consent_backfill`)
for contacts the previous release created after this ran. There is no WhatsApp equivalent: no
legacy WhatsApp marketing send exists, so WhatsApp marketing needs an explicit opt-in from here on.

Downgrade drops the ledger and the column. The legacy stores are untouched by the upgrade, so
nothing recorded before it is lost — but events recorded *after* it exist only here, and go.

Revision ID: d5a7c3e91f48
Revises: c9a2e6f1b4d7
Create Date: 2026-09-26 10:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op

revision: str = "d5a7c3e91f48"  # pragma: allowlist secret
down_revision: str | None = "c9a2e6f1b4d7"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "notification"
APP_ROLE = "opsgrids_notification"


def upgrade() -> None:
    op.create_table(
        "consent_events",
        sa.Column("channel", sa.String(length=20), nullable=False),
        sa.Column("purpose", sa.String(length=20), nullable=False),
        sa.Column("subject_enc", sa.LargeBinary(), nullable=False),
        sa.Column("subject_masked", sa.String(length=254), nullable=False),
        sa.Column("subject_index", sa.String(length=64), nullable=False),
        sa.Column("contact_id", sa.UUID(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("source", sa.String(length=40), nullable=False),
        sa.Column("evidence", sa.String(length=500), nullable=True),
        sa.Column("recorded_by", sa.UUID(), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("idempotency_key", sa.String(length=200), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.CheckConstraint(
            "channel IN ('whatsapp', 'email', 'sms')", name=op.f("ck_consent_events_channel")
        ),
        sa.CheckConstraint(
            "purpose IN ('marketing', 'transactional')", name=op.f("ck_consent_events_purpose")
        ),
        sa.CheckConstraint(
            "status IN ('opted_in', 'opted_out')", name=op.f("ck_consent_events_status")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_consent_events")),
        schema=SCHEMA,
    )
    op.create_index(
        op.f("ix_notification_consent_events_organization_id"),
        "consent_events",
        ["organization_id"],
        unique=False,
        schema=SCHEMA,
    )
    op.create_index(
        "ix_consent_events_org_subject",
        "consent_events",
        ["organization_id", "channel", "purpose", "subject_index", "occurred_at"],
        unique=False,
        schema=SCHEMA,
    )
    op.create_index(
        "ix_consent_events_org_occurred",
        "consent_events",
        ["organization_id", "occurred_at"],
        unique=False,
        schema=SCHEMA,
    )
    op.create_index(
        "uq_consent_events_org_idempotency",
        "consent_events",
        ["organization_id", "idempotency_key"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )

    op.add_column(
        "messages",
        sa.Column("suppression_reason", sa.String(length=80), nullable=True),
        schema=SCHEMA,
    )

    op.execute(f"ALTER TABLE {SCHEMA}.consent_events ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {SCHEMA}.consent_events FORCE ROW LEVEL SECURITY")
    # WITH CHECK as well as USING, or one tenant could insert rows into another's ledger
    # (ADR-0002).
    op.execute(
        f"""
        CREATE POLICY tenant_isolation ON {SCHEMA}.consent_events
            FOR ALL TO {APP_ROLE}
            USING      (organization_id = {SCHEMA}.current_org())
            WITH CHECK (organization_id = {SCHEMA}.current_org())
        """
    )
    # Append-only, as a table privilege (ADR-0000 §9). Guarded on the role existing, because a
    # database built without the per-service roles has nothing to revoke from.
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN
                REVOKE UPDATE, DELETE ON {SCHEMA}.consent_events FROM "{APP_ROLE}";
            END IF;
        END
        $$
        """
    )

    if not context.is_offline_mode():
        # Imported here, not at the top: an offline `--sql` run renders DDL and must not need the
        # application's settings just to be imported. Idempotent, and safe on an empty schema.
        from app.consent_backfill import backfill_legacy_consent

        backfill_legacy_consent(op.get_bind())


def downgrade() -> None:
    op.drop_column("messages", "suppression_reason", schema=SCHEMA)
    op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {SCHEMA}.consent_events")
    op.drop_index(
        "uq_consent_events_org_idempotency",
        table_name="consent_events",
        schema=SCHEMA,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )
    op.drop_index("ix_consent_events_org_occurred", table_name="consent_events", schema=SCHEMA)
    op.drop_index("ix_consent_events_org_subject", table_name="consent_events", schema=SCHEMA)
    op.drop_index(
        op.f("ix_notification_consent_events_organization_id"),
        table_name="consent_events",
        schema=SCHEMA,
    )
    op.drop_table("consent_events", schema=SCHEMA)

