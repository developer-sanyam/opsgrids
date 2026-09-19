"""rule contact recipient

One nullable column: the dotted path to a **contact id** in an event payload.

It is the seam that makes a proactive customer notification possible. A rule could already take
an address straight from an event (`recipient_path`) or use a fixed one (`recipient_literal`),
but a customer's real number is sealed in the CRM and notification holds no key — so the third
source names the contact and the address is resolved by asking customer-service.

Nullable with no backfill: every existing rule names one of the other two sources and is
unaffected. The "exactly one of three" rule is enforced in `domain.validate_rule` rather than by
a check constraint, because the error belongs to the operator editing a rule and needs to say
which fields conflict — a constraint violation says only that one did.

Revision ID: f39a16d28e5c
Revises: d4e5f6a7b8c9
Create Date: 2026-09-19 13:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f39a16d28e5c"  # pragma: allowlist secret
down_revision: str | None = "d4e5f6a7b8c9"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "notification"


def upgrade() -> None:
    op.add_column(
        "rules",
        sa.Column("recipient_contact_path", sa.String(length=120), nullable=True),
        schema=SCHEMA,
    )


def downgrade() -> None:
    # Any rule that targeted a contact loses its recipient and would fail `validate_rule` on the
    # next edit — which is the honest outcome of removing the only source it had, and why the
    # round-trip check runs against an empty database.
    op.drop_column("rules", "recipient_contact_path", schema=SCHEMA)
