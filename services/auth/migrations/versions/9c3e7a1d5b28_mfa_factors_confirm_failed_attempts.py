"""mfa_factors.confirm_failed_attempts

ADR-0023, second review round (m1). A pending authenticator could be confirmed by guessing: the
confirm route counted nothing, and a pending factor never expired, so a bearer token that shared the
beginning session — the only thing that may confirm it — could try six-digit codes against a factor
whose seed only the real member had seen, for as long as it stayed pending. Confirming a *first*
factor hands over ten recovery codes, so the prize was the whole account.

Confirm now counts each wrong code on the factor itself and voids the factor at the cap
(`MFA_CONFIRM_MAX_ATTEMPTS`); a pending factor also stops being confirmable ten minutes after it was
begun. The count lives on the factor rather than the credential so that mistyping a code while
setting an authenticator up does not lock the person out of signing in.

Additive, `NOT NULL DEFAULT 0` (a constant default is a metadata-only change on Postgres 11+, so the
table is not rewritten), and unused by the previous release, which keeps working against the new
schema during a rolling deploy.

Revision ID: 9c3e7a1d5b28
Revises: 5d2f8a1c7e94
Create Date: 2026-09-27 15:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "9c3e7a1d5b28"  # pragma: allowlist secret
down_revision: str | None = "5d2f8a1c7e94"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "auth"
TABLE = "mfa_factors"
COLUMN = "confirm_failed_attempts"


#: `ALTER TABLE` needs an ACCESS EXCLUSIVE lock, however briefly, and a request for it that has to
#: wait behind a long transaction makes every later query on `auth.mfa_factors` queue behind *it* —
#: which is sign-in and step-up. Fail fast instead: a deploy that cannot get the lock in five
#: seconds stops, and is run again when the long transaction is gone, rather than stalling the
#: service it is meant to update. (Local to the migration's own transaction.)
LOCK_TIMEOUT = "5s"


def upgrade() -> None:
    op.execute(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'")
    op.add_column(
        TABLE,
        sa.Column(COLUMN, sa.Integer(), nullable=False, server_default=sa.text("0")),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.execute(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'")
    op.drop_column(TABLE, COLUMN, schema=SCHEMA)
