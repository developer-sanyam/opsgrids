"""crm contact bridge on customers

Identity-bridge slice: finance keeps its own disconnected `Customer` (name/email/GST, no phone)
while `services/customer` owns real CRM contact/company identity, and operations' `Order.
customer_id` already names a CRM contact — not a finance customer. `crm_contact_id` is the link
`ensure_customer_for_crm_contact` resolves or creates against: a plain indexed `UUID`, never a
foreign key (ADR-0000 §10 — `customer` is a different schema), validated over the internal API
rather than by Postgres.

The partial unique index is the actual guarantee — the same shape `uq_employees_org_membership`
already gives employee-service's `membership_id`: at most one **live** finance customer per CRM
contact, so `ensure_customer_for_crm_contact`'s lookup-before-create never has two rows to choose
between. Partial on `crm_contact_id IS NOT NULL` because every customer entered by hand through
finance's own principal routes has none, and on `deleted_at IS NULL` so a soft-deleted row never
blocks a fresh resolve for the same contact.

Revision ID: faf3ff81d039
Revises: 57e43c60b6db
Create Date: 2026-09-29 00:01:36.027154
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "faf3ff81d039"  # pragma: allowlist secret
down_revision: str | None = "57e43c60b6db"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "finance"


def upgrade() -> None:
    op.add_column(
        "customers", sa.Column("crm_contact_id", sa.UUID(), nullable=True), schema=SCHEMA
    )
    op.create_index(
        "uq_customers_org_crm_contact",
        "customers",
        ["organization_id", "crm_contact_id"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("crm_contact_id IS NOT NULL AND deleted_at IS NULL"),
    )
    # 200 -> 254: matches CRM's own Contact.email column exactly, so a real email the bridge
    # copies over can never overflow this one (a security review caught the mismatch before
    # any row existed to be narrowed by it).
    op.alter_column(
        "customers", "email", existing_type=sa.String(200), type_=sa.String(254), schema=SCHEMA
    )


def downgrade() -> None:
    op.alter_column(
        "customers", "email", existing_type=sa.String(254), type_=sa.String(200), schema=SCHEMA
    )
    op.drop_index(
        "uq_customers_org_crm_contact",
        table_name="customers",
        schema=SCHEMA,
        postgresql_where=sa.text("crm_contact_id IS NOT NULL AND deleted_at IS NULL"),
    )
    op.drop_column("customers", "crm_contact_id", schema=SCHEMA)
