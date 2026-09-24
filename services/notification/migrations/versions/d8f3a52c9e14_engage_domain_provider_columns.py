"""engage domain provider columns

Domain verification became provider-aware: the SPF include and the DKIM shape a domain must
publish depend on the organization's connection kind (Google Workspace, Microsoft 365, or a
customer's own SMTP provider), not on a frozen record set generated when the domain was added.
Two additive, nullable-or-defaulted columns carry the two things a customer can now name for
themselves — a DKIM selector, and an SMTP provider's SPF include — so `domain.expected_records`
has something to read besides the connection kind. Nothing is dropped: `dkim_private_key_enc`
stays, unused, per its own docstring.

Revision ID: d8f3a52c9e14
Revises: b6f1c2a7d8e9
Create Date: 2026-09-24 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d8f3a52c9e14"  # pragma: allowlist secret
down_revision: str | None = "b6f1c2a7d8e9"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "notification"


def upgrade() -> None:
    op.add_column(
        "email_domains",
        sa.Column("dkim_selector", sa.String(63), nullable=False, server_default="google"),
        schema=SCHEMA,
    )
    op.alter_column("email_domains", "dkim_selector", server_default=None, schema=SCHEMA)
    op.add_column(
        "email_domains",
        sa.Column("spf_include", sa.String(255), nullable=True),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_column("email_domains", "spf_include", schema=SCHEMA)
    op.drop_column("email_domains", "dkim_selector", schema=SCHEMA)
