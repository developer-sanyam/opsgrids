"""contacts.email becomes nullable: phone-only contacts (ADR-0038 D-14)

Revision ID: a7c3e91d5b24
Revises: e230c025337e
Create Date: 2026-10-15 12:00:00.000000

Company is the party, the contact is the person, and a person is reachable by phone alone
(ADR-0038 D-14, approved 2026-10-11). A lead that carries only a phone used to convert into a
company with no contact, because `contacts.email` was NOT NULL and the code refused a
placeholder address; relaxing the column is the whole schema change.

The unique index on `(organization_id, email)` over live rows is untouched: Postgres treats NULLs
as distinct in a unique index, so any number of phone-only contacts coexist while two contacts
with the same real address are still refused.

Downgrade restores NOT NULL. A phone-only contact has no truthful address to put back, so each one
gets a unique, clearly synthetic placeholder at `<id>@no-email.invalid` first (`.invalid` is a
reserved TLD that can never be a real mailbox). The downgrade is lossy by necessity and exists so
the roundtrip check holds, not as a supported operation once phone-only contacts exist.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'a7c3e91d5b24'  # pragma: allowlist secret
down_revision: str | None = 'e230c025337e'  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "customer"


def upgrade() -> None:
    op.alter_column(
        "contacts",
        "email",
        existing_type=sa.String(length=254),
        nullable=True,
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            f"UPDATE {SCHEMA}.contacts SET email = id::text || '@no-email.invalid' "
            "WHERE email IS NULL"
        )
    )
    op.alter_column(
        "contacts",
        "email",
        existing_type=sa.String(length=254),
        nullable=False,
        schema=SCHEMA,
    )
