"""pin the OIDC endpoints and the domains that route to them

`org_idp_configs` was modelled before any flow existed, so it carries the fields a
configuration *form* needs — issuer, client id, sealed client secret, a discovery URL — and
none of the ones a sign-in needs at request time.

Four columns close that gap:

- `authorization_endpoint`, `token_endpoint`, `jwks_uri` — resolved from the provider's
  discovery document **once, when the configuration is saved**, and pinned here. Fetching
  discovery on every sign-in would put a third-party round trip between a customer and their
  login, and would let a document that changes underneath us silently repoint the flow at a
  different issuer. `discovery_url` stays, so re-resolving is a deliberate administrative act
  with a visible diff.
- `allowed_domains` — which of the organization's verified domains route to this provider.
  Empty means all of them, which is the ordinary single-provider case. It only ever narrows:
  a domain listed here that account-service has not verified still refuses, because
  verification is the property that makes domain routing safe at all (ADR-0000 §1) and a
  column in this table is not proof of anything.

Nullable and empty-defaulted, so the existing rows (there are none in any deployment, since
nothing wrote to this table until now) stay valid and an incomplete configuration simply
cannot be enabled — `services.save_sso_config` refuses that, and `services.sso_start` refuses
it again at sign-in.

Revision ID: c4f7a1e9d3b2
Revises: b7c41d9e02aa
Create Date: 2026-08-25 12:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c4f7a1e9d3b2"  # pragma: allowlist secret
down_revision: str | None = "b7c41d9e02aa"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "auth"
TABLE = "org_idp_configs"


def upgrade() -> None:
    op.add_column(
        TABLE, sa.Column("authorization_endpoint", sa.String(length=500), nullable=True), schema=SCHEMA
    )
    op.add_column(
        TABLE, sa.Column("token_endpoint", sa.String(length=500), nullable=True), schema=SCHEMA
    )
    op.add_column(TABLE, sa.Column("jwks_uri", sa.String(length=500), nullable=True), schema=SCHEMA)
    op.add_column(
        TABLE,
        sa.Column(
            "allowed_domains",
            sa.ARRAY(sa.String(length=253)),
            nullable=False,
            # A server default only so the column can be added NOT NULL without a separate
            # backfill pass. It is dropped immediately below: the model does not declare one,
            # and a default living in the database that the model does not know about is
            # schema drift `alembic check` is right to complain about.
            server_default=sa.text("'{}'::character varying[]"),
        ),
        schema=SCHEMA,
    )
    op.alter_column(TABLE, "allowed_domains", server_default=None, schema=SCHEMA)


def downgrade() -> None:
    op.drop_column(TABLE, "allowed_domains", schema=SCHEMA)
    op.drop_column(TABLE, "jwks_uri", schema=SCHEMA)
    op.drop_column(TABLE, "token_endpoint", schema=SCHEMA)
    op.drop_column(TABLE, "authorization_endpoint", schema=SCHEMA)
