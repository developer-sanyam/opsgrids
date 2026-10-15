"""tell the three CRM sales-representative roles apart

`deal`, `lead` and `customer` each seed a default rep role called "Sales representative"
(`CRM_DEAL_REP`, `CRM_LEAD_REP`, `CRM_SALES_REP`), so the invite dialog listed three identical
checkboxes. ADR-0038 D-20 merges them, but not before R2.8; until then each carries the slice it
covers in its name, the way the three administrator roles already do. The codes are frozen and
untouched.

The templates are applied by reconcile on creation only (it never renames an existing role), so
rows already seeded are renamed here. Only a system role still carrying the old name is changed;
a name an organization has edited is theirs.

Revision ID: d7f9b1c3e5a2
Revises: c22268051b11
Create Date: 2026-10-15 12:20:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d7f9b1c3e5a2"  # pragma: allowlist secret
down_revision: str | None = "c22268051b11"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD = "Sales representative"
RENAMES = (
    ("CRM_DEAL_REP", "Sales representative (pipeline)"),
    ("CRM_LEAD_REP", "Sales representative (leads)"),
    ("CRM_SALES_REP", "Sales representative (companies and contacts)"),
)


def _rename(*, code: str, from_: str, to: str) -> None:
    op.execute(
        sa.text(
            "UPDATE account.roles SET name = :to "
            "WHERE code = :code AND is_system AND name = :from_"
        ).bindparams(to=to, code=code, from_=from_)
    )


def upgrade() -> None:
    for code, new in RENAMES:
        _rename(code=code, from_=OLD, to=new)


def downgrade() -> None:
    for code, new in RENAMES:
        _rename(code=code, from_=new, to=OLD)
