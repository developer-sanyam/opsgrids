"""stop the People tagline promising payroll

The seeded tagline for `hrms` read "Attendance, leave and payroll that agree with each other".
Payroll is not built, and a customer reads this line on the marketplace and in the product's own
shell. The new line says only what ships: employees, attendance, leave and approvals.

Only a row still carrying the seeded wording is changed: a tagline an operator has since edited is
theirs and is left alone.

Revision ID: c4e6a8b0d2f1
Revises: b3d5f7a9c1e2
Create Date: 2026-10-15 12:10:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c4e6a8b0d2f1"  # pragma: allowlist secret
down_revision: str | None = "b3d5f7a9c1e2"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

KEY = "hrms"
OLD = "Attendance, leave and payroll that agree with each other"
NEW = "Employees, attendance, leave and approvals"


def _retag(*, from_: str, to: str) -> None:
    op.execute(
        sa.text(
            "UPDATE product.products SET tagline = :to WHERE key = :key AND tagline = :from_"
        ).bindparams(to=to, key=KEY, from_=from_)
    )


def upgrade() -> None:
    _retag(from_=OLD, to=NEW)


def downgrade() -> None:
    _retag(from_=NEW, to=OLD)
