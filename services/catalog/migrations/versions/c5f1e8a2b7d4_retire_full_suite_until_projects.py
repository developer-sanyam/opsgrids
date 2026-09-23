"""stop offering the suite's `full` rung until Projects launches

`f78c86fcec78` seeded the graduated ladder `duo` / `trio` / `full`, and `full` names `projects`
among its members. Projects is still `soon` in the product registry: there is no service behind
it on this deployment. Now that a bundle subscription actually entitles its members, selling
`full` would take money for a product that answers 404 — and product-service refuses to project
a `soon` member, so the customer would be billed for four products and entitled to three.

So the `full` **plan** is retired, which is what retiring is for: the customer plan read stops
offering it, and nothing is deleted. Its published version is untouched (a published version is
immutable, and a subscription pinned to it stays reconstructable). The members are **not**
edited to drop `projects` — that would be a different product under the same name, and the
ladder's graduated pricing was computed for four products.

Reversible by design: when Projects launches, the downgrade (or `POST …/plans/{id}` through the
staff console) makes `full` sellable again with nothing else to change. The deploy-time
`python -m app.bundle_audit` reports a retired rung's problems as warnings, not failures.

Revision ID: c5f1e8a2b7d4
Revises: 53f1a7a8b99d
Create Date: 2026-09-23 18:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c5f1e8a2b7d4"  # pragma: allowlist secret
down_revision: str | None = "53f1a7a8b99d"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: The `suite/full` plan's deterministic id from `f78c86fcec78` (`_oid(1, 6)`).
FULL_PLAN = "c0de0001-0000-4000-8000-000000000006"


def upgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE catalog.plans SET state = 'retired' WHERE id = CAST(:id AS uuid)"
        ).bindparams(id=FULL_PLAN)
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE catalog.plans SET state = 'active' WHERE id = CAST(:id AS uuid)"
        ).bindparams(id=FULL_PLAN)
    )
