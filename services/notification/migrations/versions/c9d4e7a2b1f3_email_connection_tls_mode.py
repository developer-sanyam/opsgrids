"""email connection tls mode

One nullable column on `email_connections`: how the connection is secured — `starttls` (an
upgrade on the plain port), `ssl` (encrypted from the first byte) or `none`.

Until now the row said only `use_tls`, and the transport read it as "call `starttls()`" — with the
stdlib's default context, which does not verify the server's certificate. The transport now always
verifies, and needs to know which of the two encrypted modes to speak: a customer whose server
offers implicit TLS on 465 could not be served at all.

Nullable with no backfill, and no data rewritten: a row with no `tls_mode` is read through
`domain.effective_tls_mode`, which derives it from `use_tls` and the port — every existing
connection was written with `use_tls` true, so every existing connection reads as STARTTLS (or
implicit TLS on 465). `none` is never stored alongside a credential, and every connection carries
one; that is enforced where the row is written (`domain.resolve_tls_mode`) rather than by a
constraint, because the refusal is a sentence for the person filling in the form.

The check constraint only pins the vocabulary. No new table, so nothing new to put under
row-level security: `email_connections` already carries its forced tenant policy.

Revision ID: c9d4e7a2b1f3
Revises: b6f1c2a7d8e9
Create Date: 2026-09-24 09:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c9d4e7a2b1f3"  # pragma: allowlist secret
down_revision: str | None = "b6f1c2a7d8e9"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "notification"


def upgrade() -> None:
    op.add_column(
        "email_connections",
        sa.Column("tls_mode", sa.String(length=16), nullable=True),
        schema=SCHEMA,
    )
    op.create_check_constraint(
        "tls_mode_known",
        "email_connections",
        "tls_mode IS NULL OR tls_mode IN ('starttls', 'ssl', 'none')",
        schema=SCHEMA,
    )


def downgrade() -> None:
    # A row that was replaced to use implicit TLS on a non-465 port reads back as STARTTLS, which
    # is the honest outcome of removing the only place that said otherwise.
    op.drop_constraint("ck_email_connections_tls_mode_known", "email_connections", schema=SCHEMA)
    op.drop_column("email_connections", "tls_mode", schema=SCHEMA)
