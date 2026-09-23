"""merge operator mfa and sso domain heads

Two slices grew this schema's migration history in parallel, both branching from
`e91a4c2f6b3d`:

* the operator MFA slice: `e2d93c244838` -> `c731a475333d` -> `5ba575e36b1a`
  (enrolment codes, the live-operator-session revocation, the separate MFA lockout counters);
* the domain-verification slice: `0c1a721d4214` (`sessions.sso_domain`).

Neither chain is re-parented — both are already applied somewhere, and rewriting a migration's
`down_revision` after the fact is how a database ends up at a revision its history no longer
contains. This revision is only the join: it changes no table, so upgrading past it is a no-op
for a database that already ran either chain, and a fresh database runs both.

Revision ID: f8e432a62a84
Revises: 0c1a721d4214, 5ba575e36b1a
Create Date: 2026-09-24 03:47:56.898426
"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "f8e432a62a84"  # pragma: allowlist secret
down_revision: str | Sequence[str] | None = (  # pragma: allowlist secret
    "0c1a721d4214",
    "5ba575e36b1a",
)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
