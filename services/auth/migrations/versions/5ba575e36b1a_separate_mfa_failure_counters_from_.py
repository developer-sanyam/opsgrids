"""separate mfa failure counters from password lockout

BLOCKER 1 of the 2026-09-23 security review, round 2: `_verify_staff` reset
`StaffAccount.failed_attempts` on a *correct password*, and MFA failures incremented that same
field — so a password right, code wrong, repeated, never crossed the threshold: the password
success kept wiping the counter every time. Password and MFA are two different proofs and need
two different counters; only a successful MFA check may clear the MFA one. Same shape added to
`Credential` for tenant users, who had no MFA failure counter at all.

`server_default='0'` on the new integer columns — the model's `default=0` is Python-side only,
and this table already has rows.

Revision ID: 5ba575e36b1a
Revises: c731a475333d
Create Date: 2026-09-23 23:18:21.027093
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "5ba575e36b1a"  # pragma: allowlist secret
down_revision: str | None = "c731a475333d"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "auth"


def upgrade() -> None:
    for table in ("credentials", "staff_accounts"):
        op.add_column(
            table,
            sa.Column(
                "mfa_failed_attempts",
                sa.Integer(),
                nullable=False,
                server_default=sa.text("0"),
            ),
            schema=SCHEMA,
        )
        op.add_column(
            table,
            sa.Column("mfa_locked_until", sa.DateTime(timezone=True), nullable=True),
            schema=SCHEMA,
        )


def downgrade() -> None:
    for table in ("staff_accounts", "credentials"):
        op.drop_column(table, "mfa_locked_until", schema=SCHEMA)
        op.drop_column(table, "mfa_failed_attempts", schema=SCHEMA)
