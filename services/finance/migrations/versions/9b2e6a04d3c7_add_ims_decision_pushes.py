"""add ims_decision_pushes

The delivery state of an IMS decision (C2): whether GSTN has been told what a person decided. One
row per `ims_decisions` row, created in the transaction that records the decision, and kept
apart from it because the two are opposite kinds of thing. A decision is evidence and is
append-only by trigger (`b62d9f4c17ae`); a push is a state machine with a lease and a retry budget,
which has to be updated. Nothing here touches a decision, and nothing about a decision changes to
say it was pushed.

The guarantees — row-level security, and a guard that stops the state machine being anything other
than the state machine — are the next migration, the same two-step shape `d4a1c93f6b28` /
`f0b7e51a2c94` used, so each can be read as one thing.

**Additive only.** A new table with in-schema foreign keys (`RESTRICT`, so neither a decision nor a
document can be removed from under a push); no existing table is altered and no existing row is
touched. Decisions recorded before this migration have no push row: they are backfilled lazily, when
a person asks for a push of their period (`services.request_ims_push`), so this migration writes
nothing and locks nothing.

**Indexes.** Everything a tenant asks leads with `organization_id`. The one exception is
`ix_ims_pushes_open_work`, which serves the reclaim sweep's discovery: that query is cross-tenant
by nature (it exists to find *which* organizations have unfinished work) and reads only unfinished
rows, so the index is partial on exactly `queued` and `submitting` and leads with `status`.
`uq_ims_pushes_org_document_current` is the invariant the read model leans on: one *current* push
per document.

Revision ID: 9b2e6a04d3c7
Revises: 4c1d8e5f7a92
Create Date: 2026-09-24 11:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "9b2e6a04d3c7"  # pragma: allowlist secret
down_revision: str | None = "4c1d8e5f7a92"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "finance"


def upgrade() -> None:
    op.create_table(
        "ims_decision_pushes",
        sa.Column("ims_document_id", sa.UUID(), nullable=False),
        sa.Column("decision_id", sa.UUID(), nullable=False),
        sa.Column("period", sa.String(length=7), nullable=False),
        sa.Column("source_ref", sa.String(length=64), nullable=False),
        sa.Column("action", sa.String(length=20), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=160), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("is_current", sa.Boolean(), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("requested_by", sa.String(length=64), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("pushed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(length=20), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("portal", sa.String(length=30), nullable=True),
        sa.Column("observed_action", sa.String(length=20), nullable=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("drift", sa.Boolean(), nullable=False),
        sa.Column("drift_detected_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.CheckConstraint(
            "action IN ('accept', 'reject', 'pending_action', 'no_action')",
            name=op.f("ck_ims_decision_pushes_action"),
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'submitting', 'pushed', 'failed', 'conflict', 'superseded')",
            name=op.f("ck_ims_decision_pushes_status"),
        ),
        sa.CheckConstraint("attempts >= 0", name=op.f("ck_ims_decision_pushes_attempts")),
        sa.CheckConstraint("version >= 1", name=op.f("ck_ims_decision_pushes_version")),
        sa.ForeignKeyConstraint(
            ["decision_id"],
            [f"{SCHEMA}.ims_decisions.id"],
            name=op.f("fk_ims_decision_pushes_decision_id_ims_decisions"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["ims_document_id"],
            [f"{SCHEMA}.ims_documents.id"],
            name=op.f("fk_ims_decision_pushes_ims_document_id_ims_documents"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ims_decision_pushes")),
        sa.UniqueConstraint("organization_id", "decision_id", name="uq_ims_pushes_org_decision"),
        sa.UniqueConstraint(
            "organization_id", "idempotency_key", name="uq_ims_pushes_org_idempotency_key"
        ),
        schema=SCHEMA,
    )
    # The bare tenant index every `TenantBase` table carries, named by the platform's convention so
    # autogenerate does not propose it again.
    op.create_index(
        op.f("ix_finance_ims_decision_pushes_organization_id"),
        "ims_decision_pushes",
        ["organization_id"],
        unique=False,
        schema=SCHEMA,
    )
    op.create_index(
        "ix_ims_pushes_org_period_status",
        "ims_decision_pushes",
        ["organization_id", "period", "status"],
        unique=False,
        schema=SCHEMA,
    )
    op.create_index(
        "uq_ims_pushes_org_document_current",
        "ims_decision_pushes",
        ["organization_id", "ims_document_id"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("is_current"),
    )
    op.create_index(
        "ix_ims_pushes_org_drift",
        "ims_decision_pushes",
        ["organization_id", "period"],
        unique=False,
        schema=SCHEMA,
        postgresql_where=sa.text("drift"),
    )
    op.create_index(
        "ix_ims_pushes_open_work",
        "ims_decision_pushes",
        ["status", "organization_id", "period"],
        unique=False,
        schema=SCHEMA,
        postgresql_where=sa.text("status IN ('queued', 'submitting')"),
    )


def downgrade() -> None:
    op.drop_table("ims_decision_pushes", schema=SCHEMA)
