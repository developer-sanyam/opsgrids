"""persist the mandate a charge debits, and close the refund-table nets

Three schema changes backing the service-layer guards that landed with them:

- `charges.mandate_id` — until now the mandate a debit ran under was sent to the provider
  and then forgotten: the charges table had no column for it, so the cap/state/currency
  rules could not be audited even in principle. Intra-schema FK to `mandates`.
- `uq_refunds_org_provider_refund_id` — partial-unique, mirroring the equivalent nets on
  `charges` and `mandates`. The refunds table is append-only by trigger, so a duplicate
  recording of one provider refund would be *uncorrectable*; now it is impossible.
- refunds `amount_minor` CHECK tightens from `>= 0` to `> 0` — a zero-amount refund row is
  a ledger entry that refunds nothing, and the service now refuses it too.

Revision ID: d4e5f6a7b8c9
Revises: b2c3d4e5f6a7
Create Date: 2026-08-25 10:30:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID as PGUUID

revision: str = "d4e5f6a7b8c9"  # pragma: allowlist secret
down_revision: str | None = "b2c3d4e5f6a7"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "payment"


def upgrade() -> None:
    op.add_column(
        "charges",
        sa.Column("mandate_id", PGUUID(as_uuid=True), nullable=True),
        schema=SCHEMA,
    )
    op.create_foreign_key(
        "fk_charges_mandate_id",
        "charges",
        "mandates",
        ["mandate_id"],
        ["id"],
        source_schema=SCHEMA,
        referent_schema=SCHEMA,
    )

    op.create_index(
        "uq_refunds_org_provider_refund_id",
        "refunds",
        ["organization_id", "provider_refund_id"],
        unique=True,
        schema=SCHEMA,
        postgresql_where="provider_refund_id IS NOT NULL",
    )

    op.drop_constraint(
        op.f("ck_refunds_amount_non_negative"), "refunds", schema=SCHEMA, type_="check"
    )
    op.create_check_constraint(
        op.f("ck_refunds_amount_positive"), "refunds", "amount_minor > 0", schema=SCHEMA
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_refunds_amount_positive"), "refunds", schema=SCHEMA, type_="check")
    op.create_check_constraint(
        op.f("ck_refunds_amount_non_negative"), "refunds", "amount_minor >= 0", schema=SCHEMA
    )

    op.drop_index("uq_refunds_org_provider_refund_id", table_name="refunds", schema=SCHEMA)

    op.drop_constraint("fk_charges_mandate_id", "charges", schema=SCHEMA, type_="foreignkey")
    op.drop_column("charges", "mandate_id", schema=SCHEMA)
