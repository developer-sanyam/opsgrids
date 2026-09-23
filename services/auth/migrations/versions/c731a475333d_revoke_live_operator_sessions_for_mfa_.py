"""revoke live operator sessions for mfa enforcement

Every operator session and refresh token in existence was minted before staff MFA became
mandatory (ADR-0003 addendum), so every one of them carries `amr = {"pwd"}` with no `otp` —
exactly the shape `require_operator` and `operator_rotate_refresh` now refuse. The runtime
checks alone would already make these dead the next time anyone tries to use them; this
migration is the deploy step that revokes them outright rather than leaving a "refused at use"
row sitting in the table looking live. A one-time act at rollout, not an ongoing rule — nothing
about this migration re-runs or re-checks anything after this deploy.

Deliberately **not reversible**: there is no session for which "still valid without ever having
proven a second factor" was ever the correct answer once this rule exists, so downgrade restores
no data — the same shape as `e91a4c2f6b3d`'s jit_provisioning correction.

Every staff account is left with no confirmed factor and no live session, exactly as if it had
never signed in — the very first sign-in after this deploy takes the enrolment path, which is
correct: an operator whose only living session was signed in and never MFA-verified never proved
they were the real staff member either.

Revision ID: c731a475333d
Revises: e2d93c244838
Create Date: 2026-09-23 21:55:27.796147
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c731a475333d"  # pragma: allowlist secret
down_revision: str | None = "e2d93c244838"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "auth"

STAFF_SESSIONS = f"""
    UPDATE "{SCHEMA}"."sessions"
    SET revoked_at = now(), revoked_reason = 'operator-mfa-enforcement-rollout'
    WHERE revoked_at IS NULL
      AND user_id IN (SELECT id FROM "{SCHEMA}"."staff_accounts")
"""

STAFF_REFRESH_TOKENS = f"""
    UPDATE "{SCHEMA}"."refresh_tokens"
    SET revoked_at = now()
    WHERE revoked_at IS NULL
      AND user_id IN (SELECT id FROM "{SCHEMA}"."staff_accounts")
"""


def upgrade() -> None:
    op.execute(sa.text(STAFF_REFRESH_TOKENS))
    op.execute(sa.text(STAFF_SESSIONS))


def downgrade() -> None:
    # Not reversed — see the module docstring. There is no prior state to restore: these
    # sessions and tokens should not have been considered fully signed in either.
    pass
