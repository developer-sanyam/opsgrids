"""seed the product catalogue

The registry is platform data, not customer data: HRMS is HRMS for everybody. It ships in a
migration rather than a seed script so that a fresh database is immediately correct and every
environment agrees on what exists — a catalogue that differs between staging and production is
a demo that works everywhere except the one that matters.

`status` is honest rather than optimistic. Only `core` is available, because it is the only one
with a service behind it; the rest are `soon` and activating them is refused, which is better
than selling a customer a product that answers 404.

Keys match the gateway route table and the first segment of every permission code. One string,
not three mappings.

Revision ID: 91001ddbbdc0
Revises: 173c32e203c1
Create Date: 2026-08-23 00:29:21.937874
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql  # noqa: F401 - autogenerate emits JSONB/UUID

revision: str = '91001ddbbdc0'
down_revision: str | None = '173c32e203c1'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


CATALOGUE = (
    # key, name, category, status, tagline, is_platform, sort
    ("core", "Atmora Core", "Platform", "available",
     "Your organization, people and access — included with everything", True, 0),
    ("hrms", "Atmora People", "People", "soon",
     "Attendance, leave and payroll that agree with each other", False, 10),
    ("crm", "Atmora Revenue", "Revenue", "soon",
     "One customer record, shared by every team that touches them", False, 20),
    ("pulseboard", "PulseBoard", "Work", "soon",
     "What the team is doing, what it cost, what it earned", False, 30),
    ("projects", "Atmora Projects", "Work", "soon",
     "Plans, boards and workflows without the rest of the suite", False, 40),
    ("support", "Atmora Support", "Revenue", "soon",
     "One queue for customers and staff, with SLAs that mean something", False, 50),
    ("marketing", "Atmora Marketing", "Revenue", "soon",
     "Campaigns measured against revenue rather than clicks", False, 60),
    ("finance", "Atmora Finance", "Finance", "soon",
     "Invoices, payments and books that reconcile themselves", False, 70),
    ("operations", "Atmora Operations", "Operations", "soon",
     "Procurement, inventory and the paperwork between them", False, 80),
    ("itsm", "Atmora IT", "Operations", "soon",
     "Assets, access requests and the joiners-movers-leavers dance", False, 90),
)


def upgrade() -> None:
    for key, name, category, status, tagline, is_platform, sort_order in CATALOGUE:
        op.execute(
            sa.text(
                """
                INSERT INTO product.products
                    (id, key, name, category, status, tagline, connects_to, features,
                     is_platform, sort_order)
                VALUES
                    (gen_random_uuid(), :key, :name, :category, :status, :tagline,
                     '[]'::jsonb, '[]'::jsonb, :is_platform, :sort_order)
                ON CONFLICT (key) DO NOTHING
                """
            ).bindparams(
                key=key,
                name=name,
                category=category,
                status=status,
                tagline=tagline,
                is_platform=is_platform,
                sort_order=sort_order,
            )
        )


def downgrade() -> None:
    """Removes only the seeded keys.

    A blanket delete would take products somebody added afterwards with them, and this
    migration has no business owning rows it did not write.
    """
    keys = tuple(row[0] for row in CATALOGUE)
    op.execute(
        sa.text("DELETE FROM product.products WHERE key = ANY(:keys)").bindparams(
            sa.bindparam("keys", value=list(keys))
        )
    )
