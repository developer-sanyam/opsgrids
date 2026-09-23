"""sessions.sso_domain

Round 3's finding: `rotate_refresh` pins a federated session to the one organization its
identity provider vouched for (`Session.idp`, `"oidc:<org>"`), but had nothing to re-check
*against* on every refresh — the domain that made the original sign-in true is not the same
fact as the organization it happened to belong to, and both a domain's verification and an
org's IdP configuration can change after the session was minted. This adds the missing column;
`app/services.py::sso_callback` fills it in at sign-in and nothing else ever changes it.

Nullable, and left `NULL` for every session that already exists: a session minted before this
column existed proved nothing about a domain the code back then even asked, so there is no
honest value to backfill, and `rotate_refresh`'s new re-check treats `NULL` as "cannot be
checked, but the pin itself is still enforced" rather than failing every session in flight
the moment this deploys.

Revision ID: 0c1a721d4214
Revises: e91a4c2f6b3d
Create Date: 2026-09-24 00:40:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0c1a721d4214"  # pragma: allowlist secret
down_revision: str | None = "e91a4c2f6b3d"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "auth"
TABLE = "sessions"
COLUMN = "sso_domain"


def upgrade() -> None:
    op.add_column(
        TABLE,
        sa.Column(COLUMN, sa.String(length=253), nullable=True),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_column(TABLE, COLUMN, schema=SCHEMA)
