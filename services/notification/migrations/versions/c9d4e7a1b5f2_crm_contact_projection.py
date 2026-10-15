"""project CRM contacts into the Engage contact book

ADR-0038 D-15, amended 2026-10-16: the Engage contact book carries a copy of each CRM contact,
**including a contact with only a phone number**. So a contact row can now lack an e-mail:
`email_enc` and `email_index` become nullable (`email_masked` stays NOT NULL and holds `''`), and
the number is stored the platform's way (`phone_enc` sealed, `phone_masked`, `phone_index` the
keyed blind index the consent ledger uses for WhatsApp, so the audience joins on equality).

`crm_contact_id` (a plain UUID, no cross-schema foreign key) and `origin` mark the copy. A
partial unique index on `(organization_id, crm_contact_id)` keeps one copy per CRM contact.
`segments.crm_status` is the new Engage segment filter.

Expand-only: every added column is nullable or defaulted, so the previous image keeps working on
the new schema. A row the old code cannot represent (a phone-only projection) only appears once
the new handler runs.

Downgrade: reverses fully on a database with no projected rows. It REFUSES while any
`origin = 'crm'` or e-mail-less row exists, because dropping the marker would turn the copy into a
manual contact the next sync duplicates and a phone-only row cannot satisfy NOT NULL; set
`OPSGRIDS_ALLOW_DESTRUCTIVE_DOWNGRADE=1` to delete those rows (campaign history and the consent
ledger are untouched) and continue.

Revision ID: c9d4e7a1b5f2
Revises: e5a1c3b7d902
Create Date: 2026-10-16 10:00:00.000000
"""

from __future__ import annotations

import os
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c9d4e7a1b5f2"  # pragma: allowlist secret
down_revision: str | None = "e5a1c3b7d902"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "notification"
ALLOW_DESTRUCTIVE_ENV = "OPSGRIDS_ALLOW_DESTRUCTIVE_DOWNGRADE"
PROJECTED = "origin = 'crm' OR email_index IS NULL"


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.add_column("contacts", sa.Column("crm_contact_id", sa.UUID(), nullable=True), schema=SCHEMA)
    op.add_column(
        "contacts",
        sa.Column(
            "origin", sa.String(length=12), server_default=sa.text("'manual'"), nullable=False
        ),
        schema=SCHEMA,
    )
    op.add_column(
        "contacts", sa.Column("phone_enc", sa.LargeBinary(), nullable=True), schema=SCHEMA
    )
    op.add_column(
        "contacts", sa.Column("phone_masked", sa.String(length=40), nullable=True), schema=SCHEMA
    )
    op.add_column(
        "contacts", sa.Column("phone_index", sa.String(length=64), nullable=True), schema=SCHEMA
    )
    op.alter_column("contacts", "email_enc", nullable=True, schema=SCHEMA)
    op.alter_column("contacts", "email_index", nullable=True, schema=SCHEMA)
    op.create_check_constraint(
        op.f("ck_contacts_origin"),
        "contacts",
        "origin IN ('manual', 'import', 'signup', 'crm')",
        schema=SCHEMA,
    )
    op.create_check_constraint(
        op.f("ck_contacts_has_an_address"),
        "contacts",
        "email_index IS NOT NULL OR phone_index IS NOT NULL",
        schema=SCHEMA,
    )
    op.create_index(
        "uq_contact_org_crm_contact",
        "contacts",
        ["organization_id", "crm_contact_id"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("crm_contact_id IS NOT NULL"),
    )
    op.create_index(
        "ix_contacts_org_phone_index",
        "contacts",
        ["organization_id", "phone_index"],
        schema=SCHEMA,
        postgresql_where=sa.text("phone_index IS NOT NULL"),
    )
    op.add_column(
        "segments", sa.Column("crm_status", sa.String(length=20), nullable=True), schema=SCHEMA
    )
    op.create_check_constraint(
        op.f("ck_segments_crm_status"),
        "segments",
        "crm_status IS NULL OR crm_status IN ('lead', 'qualified', 'customer', 'dormant')",
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    projected = (
        op.get_bind()
        .execute(sa.text(f"SELECT count(*) FROM {SCHEMA}.contacts WHERE {PROJECTED}"))
        .scalar_one()
    )
    if projected:
        if os.environ.get(ALLOW_DESTRUCTIVE_ENV) != "1":
            raise RuntimeError(
                f"{projected} projected CRM contact(s) exist; downgrading would orphan or "
                f"duplicate them. Set {ALLOW_DESTRUCTIVE_ENV}=1 to delete them and continue."
            )
        op.execute(f"DELETE FROM {SCHEMA}.contacts WHERE {PROJECTED}")
    op.drop_constraint(op.f("ck_segments_crm_status"), "segments", schema=SCHEMA, type_="check")
    op.drop_column("segments", "crm_status", schema=SCHEMA)
    op.drop_index("ix_contacts_org_phone_index", table_name="contacts", schema=SCHEMA)
    op.drop_index("uq_contact_org_crm_contact", table_name="contacts", schema=SCHEMA)
    op.drop_constraint(op.f("ck_contacts_has_an_address"), "contacts", schema=SCHEMA, type_="check")
    op.drop_constraint(op.f("ck_contacts_origin"), "contacts", schema=SCHEMA, type_="check")
    op.alter_column("contacts", "email_index", nullable=False, schema=SCHEMA)
    op.alter_column("contacts", "email_enc", nullable=False, schema=SCHEMA)
    for column in ("phone_index", "phone_masked", "phone_enc", "origin", "crm_contact_id"):
        op.drop_column("contacts", column, schema=SCHEMA)
