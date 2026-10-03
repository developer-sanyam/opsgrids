"""platform_oauth_providers — platform-wide Google/Microsoft OAuth config

Revision ID: a1f20cb97e34
Revises: 9c3e7a1d5b28
Create Date: 2026-10-03 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a1f20cb97e34"  # pragma: allowlist secret
down_revision: str | None = "9c3e7a1d5b28"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "auth"


def upgrade() -> None:
    op.create_table(
        "platform_oauth_providers",
        sa.Column("provider", sa.String(length=20), primary_key=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("client_id", sa.String(length=255), nullable=True),
        sa.Column("client_secret_enc", sa.LargeBinary(), nullable=True),
        sa.Column("issuer", sa.String(length=255), nullable=True),
        sa.Column("authorization_endpoint", sa.String(length=500), nullable=True),
        sa.Column("token_endpoint", sa.String(length=500), nullable=True),
        sa.Column("jwks_uri", sa.String(length=500), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        schema=SCHEMA,
    )
    # Deliberately NOT enabling row-level security here — this table is platform-wide,
    # the same category as `signing_keys`, which also carries no RLS policy.


def downgrade() -> None:
    op.drop_table("platform_oauth_providers", schema=SCHEMA)
