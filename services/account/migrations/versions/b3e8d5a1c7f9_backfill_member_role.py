"""give core MEMBER to members invited with only product roles

`add_member` used to replace the default roles with an explicit list, so an invitee given only a
product role (EMPLOYEE, ...) never held core MEMBER, and with it own approvals and location /
organization read. `add_member` now adds MEMBER; this fixes the members already invited.

Per organization: the MEMBER role of the membership's own organization. Only memberships that
hold at least one product role and no core role at all: an owner, admin or approval admin is
untouched, and so is a membership whose roles were all removed. Idempotent (NOT EXISTS plus the
pair's unique constraint). Grants only; a token picks MEMBER up at its next refresh.

Downgrade is a no-op: a backfilled row cannot be told apart from a MEMBER granted later.

Revision ID: b3e8d5a1c7f9
Revises: d7f9b1c3e5a2
Create Date: 2026-10-16 12:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b3e8d5a1c7f9"  # pragma: allowlist secret
down_revision: str | None = "d7f9b1c3e5a2"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

BACKFILL = sa.text(
    """
    INSERT INTO account.membership_roles (id, membership_id, role_id)
    SELECT gen_random_uuid(), m.id, member.id
    FROM account.memberships m
    JOIN account.roles member
      ON member.organization_id = m.organization_id AND member.code = 'MEMBER'
    WHERE m.status <> 'removed'
      AND EXISTS (
        SELECT 1 FROM account.membership_roles mr
        JOIN account.roles r ON r.id = mr.role_id
        WHERE mr.membership_id = m.id AND r.product <> 'core'
      )
      AND NOT EXISTS (
        SELECT 1 FROM account.membership_roles mr
        JOIN account.roles r ON r.id = mr.role_id
        WHERE mr.membership_id = m.id AND r.product = 'core'
      )
    ON CONFLICT (membership_id, role_id) DO NOTHING
    """
)


def upgrade() -> None:
    op.execute(BACKFILL)


def downgrade() -> None:
    pass
