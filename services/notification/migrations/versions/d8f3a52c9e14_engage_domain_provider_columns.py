"""engage domain provider columns

Domain verification became provider-aware: the SPF include and the DKIM shape a domain must
publish depend on the organization's connection kind (Google Workspace, Microsoft 365, or a
customer's own SMTP provider), not on a frozen record set generated when the domain was added.
Two additive, nullable-or-defaulted columns carry the two things a customer can now name for
themselves — a DKIM selector, and an SMTP provider's SPF include — so `domain.expected_records`
has something to read besides the connection kind. Nothing is dropped: `dkim_private_key_enc`
stays, unused, per its own docstring.

**Data reset, not just a schema change.** A domain added before this fix holds `records` in the
pre-fix shape — each entry has `host`/`verified` keys instead of `fqdn`/`status`, and includes
`return_path`/`tracking` kinds that no longer exist. `DnsRecordOut(**record)` raises on that
shape, which turns one such row into a 500 for every read of the organization's whole domain
list (`_domain_out` in `app/api/engage.py` is defensive against this too, belt and suspenders —
this reset is the belt). `RESET_LEGACY_DOMAINS_SQL` below finds every row whose first record has
no `status` key — the one field no pre-fix record ever carried — and:

- empties `records`, so the next read previews the current provider's requirements instead of
  raising on the old ones;
- drops `status='verified'` to `'pending'` — a domain "verified" against instructions that
  pointed at OpsGrids infrastructure that never existed proved nothing, and letting it stand
  would let a sender go on being added against a domain nobody has actually verified under the
  rules that now apply; any other status is left alone;
- nulls `dkim_private_key_enc` — the keypair this service used to mint and never actually signed
  anything with, now unused dead weight in a column that holds the most dangerous value in the
  schema.

The `WHERE` clause only ever matches a pre-fix row: a domain added or re-verified under the
current code always writes at least one record with a `status` key (even an empty `records`
list read by a domain the migration itself just touched stays empty, since `records <>
'[]'::jsonb` excludes it). `downgrade()` stays column-only — the data this reset clears was never
correct under the old code's own rules either, so there is nothing to restore.

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

#: Matches only a pre-fix row — one whose first stored record has no `status` key, the field
#: every record this service writes now always carries. An empty `records` array never matches
#: (`records <> '[]'::jsonb` excludes it before the `-> 0 ?` ever runs on it), so a domain that
#: has never been checked, or one this statement already reset, is left alone.
RESET_LEGACY_DOMAINS_SQL = f"""
    UPDATE {SCHEMA}.email_domains
    SET records = '[]'::jsonb,
        status = CASE WHEN status = 'verified' THEN 'pending' ELSE status END,
        dkim_private_key_enc = NULL
    WHERE records <> '[]'::jsonb
      AND NOT (records -> 0 ? 'status')
"""


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
    op.execute(RESET_LEGACY_DOMAINS_SQL)


def downgrade() -> None:
    op.drop_column("email_domains", "spf_include", schema=SCHEMA)
    op.drop_column("email_domains", "dkim_selector", schema=SCHEMA)
