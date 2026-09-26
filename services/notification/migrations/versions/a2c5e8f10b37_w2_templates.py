"""whatsapp templates: what Meta returns, and the history of how it got there

`provider_templates` already holds the wording Meta reviews (`preview_text`), its answer
(`status`, `status_reason`), its id (`provider_template_id`) and the mapping the send path reads
(`variables`). This adds what a synced or submitted template also carries:

- `quality`: Meta's own quality rating for the template, kept in our vocabulary
  (`high|medium|low|unknown`).
- `components`: Meta's components JSON, whole, so the editor can show exactly what was submitted.
- `variable_specs`: `[{index, name, kind, example}]`, the authoring view of the placeholders. It is
  a separate column from `variables` on purpose: `variables` is the send path's mapping to *our*
  payload keys (`{key, component, position, ...}`) and is read with `TemplateVariable(**v)`, so a
  second shape in it would break every send.
- `submitted_at`: when we last handed the template to Meta.
- `history`: the status changes, `[{status, at, source}]`, appended to and capped. A column rather
  than a table because it is at most a few dozen small entries per template and is only ever read
  together with its template.

All additive with server defaults, so it rewrites no existing row's meaning. Row-level security is
already on the table and covers the new columns.

Revision ID: a2c5e8f10b37
Revises: d5a7c3e91f48
Create Date: 2026-09-26 11:40:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a2c5e8f10b37"  # pragma: allowlist secret
down_revision: str | None = "d5a7c3e91f48"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "notification"


def upgrade() -> None:
    op.add_column(
        "provider_templates",
        sa.Column("quality", sa.String(length=20), server_default="unknown", nullable=False),
        schema=SCHEMA,
    )
    op.add_column(
        "provider_templates",
        sa.Column(
            "components",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        schema=SCHEMA,
    )
    op.add_column(
        "provider_templates",
        sa.Column(
            "variable_specs",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        schema=SCHEMA,
    )
    op.add_column(
        "provider_templates",
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "provider_templates",
        sa.Column(
            "history",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        schema=SCHEMA,
    )
    # A webhook names a template by Meta's id; leading with the tenant column like every index
    # here, because the RLS policy qual is ANDed into every query (ADR-0002).
    op.create_index(
        "ix_provider_templates_org_meta_id",
        "provider_templates",
        ["organization_id", "provider_template_id"],
        unique=False,
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_provider_templates_org_meta_id", table_name="provider_templates", schema=SCHEMA
    )
    op.drop_column("provider_templates", "history", schema=SCHEMA)
    op.drop_column("provider_templates", "submitted_at", schema=SCHEMA)
    op.drop_column("provider_templates", "variable_specs", schema=SCHEMA)
    op.drop_column("provider_templates", "components", schema=SCHEMA)
    op.drop_column("provider_templates", "quality", schema=SCHEMA)
