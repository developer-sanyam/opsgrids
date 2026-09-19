"""store identity: brand, custom domain claim

ADR-0011. The brand moves out of the theme document and onto the identity row, and
`custom_domain` stops being an unread placeholder and becomes a merchant-writable *claim*.

**`name` is added in three steps rather than one**, which is the only interesting thing in this
file. `ADD COLUMN … NOT NULL` with no default fails outright on a table that already has rows,
and a server default would leave every existing store named after the default forever — a value
nobody typed, indistinguishable from one somebody did. So: add it nullable, backfill it from the
slug (the only identity an existing row has), then set it `NOT NULL`. A merchant whose store was
created before this migration sees their slug in the name box and edits it; nothing is invented
and nothing is silently blank.

**The partial unique index on `custom_domain` is brought forward from slice 2.** ADR-0010 §10's
index-timing register put it "with custom domains", and this is where custom domains arrive — a
merchant can write the field from here, so two tenants can name the same domain from here. It is
partial (`WHERE custom_domain IS NOT NULL`) because null is not a claim: without that, every
storefront that has named no domain would collide with every other one.

**What this migration does not add, deliberately**: any verification state a merchant could
reach, any token column, and any routing. `custom_domain_status` is `unverified` or null, and
there is nothing in this platform that serves a custom domain — no ingress, no TLS termination,
no host-based routing (ADR-0011 §5). `verified` is in the check constraint from day one so the
hosting slice is a validation change rather than an ALTER on a table with live tenant rows.

The five check constraints are the writer's rules written where a backfill or a hand-written
UPDATE cannot skip them: brand text is trimmed and non-blank, a domain is a lower-cased hostname
of at least two labels, and a status exists exactly when a domain does.

Row-level security is untouched. `storefronts` already carries `tenant_isolation` with `USING`
and `WITH CHECK`, and adding columns to a policied table changes nothing about the policy.

Revision ID: af1b5d14f90d
Revises: c71a5e0f4b82
Create Date: 2026-09-19 15:22:37.788627
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql  # noqa: F401 - autogenerate emits JSONB/UUID

revision: str = 'af1b5d14f90d'  # pragma: allowlist secret — an alembic revision id
down_revision: str | None = 'c71a5e0f4b82'  # pragma: allowlist secret — an alembic revision id
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "storefront"


def upgrade() -> None:
    # Nullable first: the table may already hold rows, and there is no honest default for a
    # shop's name.
    op.add_column('storefronts', sa.Column('name', sa.String(length=80), nullable=True), schema=SCHEMA)
    # The slug is the only identity an existing row has, and it is what the merchant chose. It
    # is already `^[a-z0-9][a-z0-9-]{1,62}$`, so it satisfies the trimmed/non-blank constraint
    # added below without any cleaning.
    op.execute(f"UPDATE {SCHEMA}.storefronts SET name = slug WHERE name IS NULL")
    op.alter_column('storefronts', 'name', nullable=False, schema=SCHEMA)

    op.add_column('storefronts', sa.Column('tagline', sa.String(length=160), nullable=True), schema=SCHEMA)
    op.add_column('storefronts', sa.Column('logo_text', sa.String(length=12), nullable=True), schema=SCHEMA)
    op.add_column('storefronts', sa.Column('custom_domain_status', sa.String(length=16), nullable=True), schema=SCHEMA)

    op.create_index('uq_storefronts_custom_domain', 'storefronts', ['custom_domain'], unique=True, schema=SCHEMA, postgresql_where=sa.text('custom_domain IS NOT NULL'))
    op.create_check_constraint(op.f('ck_storefronts_custom_domain_shape'), 'storefronts', "custom_domain IS NULL OR custom_domain ~ '^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$'", schema=SCHEMA)
    op.create_check_constraint(op.f('ck_storefronts_custom_domain_status'), 'storefronts', "(custom_domain IS NULL AND custom_domain_status IS NULL) OR (custom_domain IS NOT NULL AND custom_domain_status IN ('unverified', 'verified'))", schema=SCHEMA)
    op.create_check_constraint(op.f('ck_storefronts_logo_text_is_trimmed'), 'storefronts', 'logo_text IS NULL OR (length(logo_text) > 0 AND logo_text = btrim(logo_text))', schema=SCHEMA)
    op.create_check_constraint(op.f('ck_storefronts_name_is_trimmed'), 'storefronts', 'length(name) > 0 AND name = btrim(name)', schema=SCHEMA)
    op.create_check_constraint(op.f('ck_storefronts_tagline_is_trimmed'), 'storefronts', 'tagline IS NULL OR (length(tagline) > 0 AND tagline = btrim(tagline))', schema=SCHEMA)


def downgrade() -> None:
    # Exactly what upgrade() added, in reverse. A store's name, tagline, logo text and domain
    # claim are lost — there is nowhere else to put them — which is the honest cost of rolling
    # this back and the reason the brand's move is an ADR rather than a refactor.
    op.drop_constraint(op.f('ck_storefronts_tagline_is_trimmed'), 'storefronts', schema=SCHEMA, type_='check')
    op.drop_constraint(op.f('ck_storefronts_name_is_trimmed'), 'storefronts', schema=SCHEMA, type_='check')
    op.drop_constraint(op.f('ck_storefronts_logo_text_is_trimmed'), 'storefronts', schema=SCHEMA, type_='check')
    op.drop_constraint(op.f('ck_storefronts_custom_domain_status'), 'storefronts', schema=SCHEMA, type_='check')
    op.drop_constraint(op.f('ck_storefronts_custom_domain_shape'), 'storefronts', schema=SCHEMA, type_='check')
    op.drop_index('uq_storefronts_custom_domain', table_name='storefronts', schema=SCHEMA, postgresql_where=sa.text('custom_domain IS NOT NULL'))
    op.drop_column('storefronts', 'custom_domain_status', schema=SCHEMA)
    op.drop_column('storefronts', 'logo_text', schema=SCHEMA)
    op.drop_column('storefronts', 'tagline', schema=SCHEMA)
    op.drop_column('storefronts', 'name', schema=SCHEMA)
