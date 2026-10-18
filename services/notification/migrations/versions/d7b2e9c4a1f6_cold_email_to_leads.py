"""cold e-mail to CRM leads: contact.crm_lead_id, origin crm_lead, campaigns.cold_outreach

ADR-0038 addendum 2026-10-16. A lead with an e-mail can be copied into the Engage contact book
(`origin = 'crm_lead'`, `crm_lead_id` a plain UUID, one copy per lead) and mailed by a campaign
whose `cold_outreach` flag is on. The flag changes one rule: the audience needs "not opted out"
instead of "opted in", and only for a contact carrying a `crm_lead_id`.

Expand-only: the new columns are nullable or defaulted and the widened check is a superset, so the
previous image keeps working. Downgrade refuses while any lead copy or cold campaign exists (the
copy would become an unmarked contact, and a cold campaign would then need consent it never had);
`OPSGRIDS_ALLOW_DESTRUCTIVE_DOWNGRADE=1` deletes the lead copies and the cold campaigns' flag.

Revision ID: d7b2e9c4a1f6
Revises: c9d4e7a1b5f2
Create Date: 2026-10-16 14:00:00.000000
"""

from __future__ import annotations

import os
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d7b2e9c4a1f6"  # pragma: allowlist secret
down_revision: str | None = "c9d4e7a1b5f2"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "notification"
ALLOW_DESTRUCTIVE_ENV = "OPSGRIDS_ALLOW_DESTRUCTIVE_DOWNGRADE"


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.add_column("contacts", sa.Column("crm_lead_id", sa.UUID(), nullable=True), schema=SCHEMA)
    op.drop_constraint(op.f("ck_contacts_origin"), "contacts", schema=SCHEMA, type_="check")
    op.create_check_constraint(
        op.f("ck_contacts_origin"),
        "contacts",
        "origin IN ('manual', 'import', 'signup', 'crm', 'crm_lead')",
        schema=SCHEMA,
    )
    op.create_index(
        "uq_contact_org_crm_lead",
        "contacts",
        ["organization_id", "crm_lead_id"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("crm_lead_id IS NOT NULL"),
    )
    op.add_column(
        "campaigns",
        sa.Column("cold_outreach", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    bind = op.get_bind()
    used = bind.execute(
        sa.text(
            f"SELECT (SELECT count(*) FROM {SCHEMA}.contacts WHERE crm_lead_id IS NOT NULL "
            f"OR origin = 'crm_lead') + (SELECT count(*) FROM {SCHEMA}.campaigns "
            "WHERE cold_outreach)"
        )
    ).scalar_one()
    if used:
        if os.environ.get(ALLOW_DESTRUCTIVE_ENV) != "1":
            raise RuntimeError(
                f"{used} lead cold-email row(s) exist; downgrading would orphan them. "
                f"Set {ALLOW_DESTRUCTIVE_ENV}=1 to delete the lead copies and continue."
            )
        op.execute(f"DELETE FROM {SCHEMA}.contacts WHERE crm_lead_id IS NOT NULL OR origin = 'crm_lead'")
    op.drop_column("campaigns", "cold_outreach", schema=SCHEMA)
    op.drop_index("uq_contact_org_crm_lead", table_name="contacts", schema=SCHEMA)
    op.drop_constraint(op.f("ck_contacts_origin"), "contacts", schema=SCHEMA, type_="check")
    op.create_check_constraint(
        op.f("ck_contacts_origin"),
        "contacts",
        "origin IN ('manual', 'import', 'signup', 'crm')",
        schema=SCHEMA,
    )
    op.drop_column("contacts", "crm_lead_id", schema=SCHEMA)
