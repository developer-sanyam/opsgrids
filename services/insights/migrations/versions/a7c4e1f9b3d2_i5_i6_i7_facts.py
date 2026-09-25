"""I5/I6/I7 entity facts: purchase_order_facts, task_facts, enquiry_facts, enquiry_line_facts

Additive only — insights' migrations create only its own schema (ADR-0020 build plan). Every
new table gets `ENABLE`/`FORCE ROW LEVEL SECURITY` and the same `tenant_isolation` policy shape
`5cead06f3d54` already set up, through this schema's own `insights.current_org()` (never a
shared `public` helper).

`enquiry_line_facts` has no `organization_id`-leading primary key of its own beyond the natural
key `(organization_id, enquiry_id, line_no)` and a real in-schema FK to `enquiry_facts` `ON
DELETE CASCADE` — the one place this schema uses a foreign key at all, and it never crosses a
schema boundary (CLAUDE.md).

Revision ID: a7c4e1f9b3d2
Revises: 5cead06f3d54
Create Date: 2026-09-25 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a7c4e1f9b3d2"  # pragma: allowlist secret
down_revision: str | None = "5cead06f3d54"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "insights"
APP_ROLE = "opsgrids_insights"

#: The new tables this migration adds, all tenant-isolated the same way.
NEW_TABLES = ("purchase_order_facts", "task_facts", "enquiry_facts", "enquiry_line_facts")


def _fact_columns() -> list[sa.Column]:
    """The five columns every `*_facts` table carries (`app/models.py:FactMixin`,
    data-model.md §1)."""
    return [
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_seen_run", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "ingested_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
    ]


def _enable_rls(table: str) -> None:
    op.execute(f'ALTER TABLE {SCHEMA}."{table}" ENABLE ROW LEVEL SECURITY')
    op.execute(f'ALTER TABLE {SCHEMA}."{table}" FORCE ROW LEVEL SECURITY')
    op.execute(
        f"""
        CREATE POLICY tenant_isolation ON {SCHEMA}."{table}"
            FOR ALL TO {APP_ROLE}
            USING      (organization_id = {SCHEMA}.current_org())
            WITH CHECK (organization_id = {SCHEMA}.current_org())
        """
    )


def upgrade() -> None:
    op.create_table(
        "purchase_order_facts",
        *_fact_columns(),
        sa.Column("number", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("supplier_ref", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("supplier_name", sa.Text(), nullable=False),
        sa.Column("owner_ref", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_date", sa.Date(), nullable=False),
        sa.Column("expected_date", sa.Date(), nullable=False),
        sa.Column("received_date", sa.Date(), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("total_minor", sa.BigInteger(), nullable=False),
        sa.CheckConstraint(
            "currency ~ '^[A-Z]{3}$'", name=op.f("ck_purchase_order_facts_currency_shape")
        ),
        sa.PrimaryKeyConstraint(
            "organization_id", "id", name=op.f("pk_purchase_order_facts")
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_purchase_order_facts_org_created_date",
        "purchase_order_facts",
        ["organization_id", "created_date"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_purchase_order_facts_org_received_date",
        "purchase_order_facts",
        ["organization_id", "received_date"],
        schema=SCHEMA,
        postgresql_where=sa.text("received_date IS NOT NULL"),
    )
    op.create_index(
        "ix_purchase_order_facts_org_source_created_at",
        "purchase_order_facts",
        ["organization_id", sa.literal_column("source_created_at DESC")],
        schema=SCHEMA,
    )

    op.create_table(
        "task_facts",
        *_fact_columns(),
        sa.Column("project_ref", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("stage", sa.Text(), nullable=False),
        sa.Column("priority", sa.Text(), nullable=False),
        sa.Column("points", sa.Integer(), nullable=False),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("owner_ref", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("organization_id", "id", name=op.f("pk_task_facts")),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_task_facts_org_completed_at",
        "task_facts",
        ["organization_id", "completed_at"],
        schema=SCHEMA,
        postgresql_where=sa.text("completed_at IS NOT NULL"),
    )
    op.create_index(
        "ix_task_facts_org_source_created_at",
        "task_facts",
        ["organization_id", "source_created_at"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_task_facts_org_owner_open",
        "task_facts",
        ["organization_id", "owner_ref"],
        schema=SCHEMA,
        postgresql_where=sa.text("deleted_at IS NULL AND stage <> 'done'"),
    )

    op.create_table(
        "enquiry_facts",
        *_fact_columns(),
        sa.Column("store_ref", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("company", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("line_count", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("organization_id", "id", name=op.f("pk_enquiry_facts")),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_enquiry_facts_org_source_created_at",
        "enquiry_facts",
        ["organization_id", sa.literal_column("source_created_at DESC")],
        schema=SCHEMA,
    )

    op.create_table(
        "enquiry_line_facts",
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("enquiry_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("line_no", sa.Integer(), nullable=False),
        sa.Column("product_ref", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("product_name", sa.Text(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint(
            "organization_id", "enquiry_id", "line_no", name=op.f("pk_enquiry_line_facts")
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "enquiry_id"],
            ["insights.enquiry_facts.organization_id", "insights.enquiry_facts.id"],
            name="fk_enquiry_line_facts_enquiry",
            ondelete="CASCADE",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_enquiry_line_facts_org_product",
        "enquiry_line_facts",
        ["organization_id", "product_ref"],
        schema=SCHEMA,
    )

    for table in NEW_TABLES:
        _enable_rls(table)


def downgrade() -> None:
    for table in NEW_TABLES:
        op.execute(f'DROP POLICY IF EXISTS tenant_isolation ON {SCHEMA}."{table}"')
        op.execute(f'ALTER TABLE {SCHEMA}."{table}" NO FORCE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE {SCHEMA}."{table}" DISABLE ROW LEVEL SECURITY')

    op.drop_index("ix_enquiry_line_facts_org_product", table_name="enquiry_line_facts", schema=SCHEMA)
    op.drop_table("enquiry_line_facts", schema=SCHEMA)

    op.drop_index(
        "ix_enquiry_facts_org_source_created_at", table_name="enquiry_facts", schema=SCHEMA
    )
    op.drop_table("enquiry_facts", schema=SCHEMA)

    op.drop_index("ix_task_facts_org_owner_open", table_name="task_facts", schema=SCHEMA)
    op.drop_index(
        "ix_task_facts_org_source_created_at", table_name="task_facts", schema=SCHEMA
    )
    op.drop_index("ix_task_facts_org_completed_at", table_name="task_facts", schema=SCHEMA)
    op.drop_table("task_facts", schema=SCHEMA)

    op.drop_index(
        "ix_purchase_order_facts_org_source_created_at",
        table_name="purchase_order_facts",
        schema=SCHEMA,
    )
    op.drop_index(
        "ix_purchase_order_facts_org_received_date",
        table_name="purchase_order_facts",
        schema=SCHEMA,
    )
    op.drop_index(
        "ix_purchase_order_facts_org_created_date",
        table_name="purchase_order_facts",
        schema=SCHEMA,
    )
    op.drop_table("purchase_order_facts", schema=SCHEMA)
