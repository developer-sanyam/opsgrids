"""mfa_factors.enrolled_session_id

ADR-0023 (member step-up), finding B1 of its security review: a bearer token alone could start
an enrolment, confirm it with the attacker's own authenticator, and then pass step-up with it.
Enrolment now needs the member's password (and a code from an existing factor), and the factor
remembers *which session* did that: `confirm` accepts a pending factor only from the session that
began it, and `step_up` accepts only a factor that predates the caller's session or was enrolled
through it.

Nullable, and left `NULL` for every factor that already exists. That is correct rather than
merely tolerated: a factor confirmed before the session that presents it began is accepted on its
timestamp alone, so no existing account loses anything. The two visible effects are both fail-closed
and both small. A member who enrolled inside a session that is *still open across this deploy*
(sessions last at most seven days) is told to sign in again before that session can step up; and a
factor that was begun but never confirmed before the deploy cannot be confirmed afterwards and has
to be started again. No backfill is attempted — there is no record to backfill from.

Additive and nullable, so the previous release keeps working against the new schema during a
rolling deploy.

Revision ID: 5d2f8a1c7e94
Revises: f8e432a62a84
Create Date: 2026-09-27 12:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "5d2f8a1c7e94"  # pragma: allowlist secret
down_revision: str | None = "f8e432a62a84"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "auth"
TABLE = "mfa_factors"
COLUMN = "enrolled_session_id"


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
        sa.Column(COLUMN, postgresql.UUID(as_uuid=True), nullable=True),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.execute(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'")
    op.drop_column(TABLE, COLUMN, schema=SCHEMA)
