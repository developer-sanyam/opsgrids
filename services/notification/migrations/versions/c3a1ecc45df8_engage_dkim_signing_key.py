"""engage dkim signing key

The DKIM record generated with the rest of a domain's record set has an empty value until a
keypair exists to fill it — `domain.dns_records_for` is pure and cannot mint one. The key is
minted where the domain is created and the private half is stored here, sealed by
`platform_core.pii.seal`, so the public half can go into the record the customer publishes.

Bytes rather than text, and nullable, for the same reasons as `email_connections.secret_enc`:
what is stored is an envelope, not something anybody should be tempted to read, and there is no
default value a private key could take.

Revision ID: c3a1ecc45df8
Revises: 6c89f77c9fda
Create Date: 2026-09-21 16:28:35.780772
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c3a1ecc45df8"  # pragma: allowlist secret
down_revision: str | None = "6c89f77c9fda"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "notification"


def upgrade() -> None:
    op.add_column(
        "email_domains",
        sa.Column("dkim_private_key_enc", sa.LargeBinary(), nullable=True),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_column("email_domains", "dkim_private_key_enc", schema=SCHEMA)
