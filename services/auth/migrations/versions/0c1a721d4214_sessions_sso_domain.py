"""sessions.sso_domain

Round 3's finding: `rotate_refresh` pins a federated session to the one organization its
identity provider vouched for (`Session.idp`, `"oidc:<org>"`), but had nothing to re-check
*against* on every refresh — the domain that made the original sign-in true is not the same
fact as the organization it happened to belong to, and both a domain's verification and an
org's IdP configuration can change after the session was minted. This adds the missing column;
`app/services.py::sso_callback` fills it in at sign-in and nothing else ever changes it.

Nullable, and left `NULL` for every session that already exists. No pre-existing *federated*
session exists to be left `NULL`, though: `sso_start` could never succeed for a real customer
before this slice (nothing could verify a domain, so nothing could route a sign-in), so every
row in `sessions` today is a password session, for which `NULL` is simply correct. A federated
session that is somehow `NULL` anyway cannot be re-checked against anything, so
`rotate_refresh` and `switch_organization` treat it as **no longer valid** and end it — failing
closed, at no real cost — rather than assuming it still holds.

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
