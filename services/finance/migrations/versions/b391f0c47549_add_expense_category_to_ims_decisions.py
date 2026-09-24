"""add expense_category to ims_decisions

The one classification the ITC ledger's accrual needs that nothing upstream can supply.
`ims_documents` — what a supplier filed, as the portal shows it — carries no HSN, no description
and no line items, only a supplier, a document and four money columns. There is therefore nothing
in the data to *derive* a Section 17(5) blocked-credit category from, so this slice lets the
person accepting (or rejecting, or parking) a document say what it was for, and the ITC ledger
posts eligible credit only when the category is not one of the ones the statute blocks —
`domain.BLOCKED_ITC_CATEGORIES`.

Added to `ims_decisions` rather than `ims_documents`: this is a judgement recorded about a
document, the same shape `note` already is, not a fact about the document itself — and
`ims_decisions` is where evidence about a decision belongs. `ims_decisions` is append-only by
trigger (see `b62d9f4c17ae`), but that trigger only refuses UPDATE and DELETE of existing rows; it
has no bearing on a DDL `ALTER TABLE`, and adding a column with a constant default does not
rewrite or touch any existing row in modern PostgreSQL.

Revision ID: b391f0c47549
Revises: ddbdd3acc173
Create Date: 2026-09-23 09:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b391f0c47549"  # pragma: allowlist secret
down_revision: str | None = "ddbdd3acc173"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "finance"


def upgrade() -> None:
    op.add_column(
        "ims_decisions",
        sa.Column(
            "expense_category", sa.String(length=40), nullable=False, server_default="general"
        ),
        schema=SCHEMA,
    )
    # The model carries no default at the column level once every existing row is filled — the
    # same shape the GST-facts and withholding-facts migrations used, so autogenerate does not
    # propose removing a default the model never declared.
    op.alter_column("ims_decisions", "expense_category", server_default=None, schema=SCHEMA)


def downgrade() -> None:
    op.drop_column("ims_decisions", "expense_category", schema=SCHEMA)
